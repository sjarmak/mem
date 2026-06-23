import { CommandContext } from '../index.js';
import { storePath } from '../store.js';
import { openStore, writeRecords } from '../../store/index.js';
import { defaultConnection, doltRunner, listRigs, readRig } from '../../ingest/beads.js';
import {
  type SessionResolver,
  attachTraceRefs,
  gcSessionResolver,
} from '../../ingest/trace-resolve.js';
import {
  type TranscriptArchive,
  defaultArchiveRoot,
  loadTranscriptArchive,
} from '../../ingest/trace-archive.js';
import { attachProvenance } from '../../ingest/provenance.js';
import { attachCommitOutcomes } from '../../ingest/commitLinkage.js';
import { attachLanded } from '../../ingest/landed.js';
import { attachRepo } from '../../ingest/repo-resolve.js';
import {
  type SessionJoin,
  attachSessionJoin,
  loadSessionJoin,
} from '../../ingest/session-merge.js';
import {
  type TaskTypeArtifact,
  type TaskTypeEntry,
  attachTaskTypes,
  loadTaskTypes,
} from '../../ingest/task-type.js';
import { type TraceReader, parseRecordTrace } from '../../parse/trace-parse.js';
import type { WorkRecord } from '../../schemas/workrecord.js';

export interface BuildStoreResult {
  store: string;
  rig: string | null;
  count: number;
  /** Records carrying ≥1 deterministic trace error after the P1.6 parse (0 when
   * `--with-traces` is off). The D8 failure signatures the `ours` arm fires on. */
  records_with_errors: number;
  /** Records that resolved a canonical `repo` (mem-bme): from a PR outcome or
   * the rig→repo map. The complement (`count - records_with_repo`) is the
   * `unmapped` residue a coverage probe should chase down. */
  records_with_repo: number;
  /** Records carrying a work_dir, so a git baseline was attempted (0 when
   * `--with-provenance` is off). */
  records_with_provenance: number;
  /** Subset of the above whose session-start commit resolved by date — the
   * git-checkout anchors a future real-exec replay can use. */
  records_with_commit: number;
  /** Records whose work→landing-commit outcome resolved (`outcome.commit_sha`,
   * the recovered squash/landing commit; the `with_commit_sha` coverage axis).
   * 0 when `--with-provenance` is off. */
  records_with_commit_sha: number;
  /** Records whose work landed on the integration branch and survived
   * (`landed.landed_state === 'landed'`); the forward outcome signal for the
   * direct-to-main corpus (0 when `--with-provenance` is off). */
  records_landed: number;
  /** Records that received ≥2 session iterations from the merged session-join
   * artifact (0 when `--session-join` is off). */
  records_multi_session: number;
}

/** Options for {@link attachAndParse} — injectable so the P1.3→P1.6 wiring is
 * testable without a `gc` binary or real transcripts on disk. */
export interface AttachParseDeps {
  resolve?: SessionResolver;
  read?: TraceReader;
  /** Transcript archive for reaped-transcript recovery (mem-h3di.4). */
  archive?: TranscriptArchive;
}

/**
 * Resolve each record's transcript (P1.3 `attachTraceRefs`: assignee → session →
 * JSONL) and parse its deterministic build/test/lint failure signal (P1.6
 * `parseRecordTrace`). Pure composition of the two existing primitives — it adds
 * no extraction logic of its own, so the ZFC and validity guarantees of P1.6
 * (deterministic tool-output errors only; never the outcome label) carry through
 * unchanged. Records are copied, never mutated.
 */
export function attachAndParse(records: WorkRecord[], deps: AttachParseDeps = {}): WorkRecord[] {
  const resolved = attachTraceRefs(records, {
    ...(deps.resolve && { resolve: deps.resolve }),
    ...(deps.archive && { archive: deps.archive }),
  });
  return resolved.map(record => parseRecordTrace(record, deps.read));
}

/**
 * Materialize the P1.5 SQLite+FTS5 sidecar from the dolt bead spine — the
 * write counterpart to the read-only query commands. Reuses the existing ingest
 * readers and {@link writeRecords}; it adds no new substrate, only the wiring
 * that puts real WorkRecords into the store the retrieval/eval path reads.
 *
 * {@link openStore} creates the parent dir (a fresh checkout has no `.mem/`)
 * and initializes the schema on a new file. The single write lifecycle for
 * both the per-rig command loop and tests, so the persistence half is
 * exercised without a live dolt connection.
 */
export function buildStoreFromRecords(path: string, records: WorkRecord[]): number {
  const db = openStore(path);
  try {
    writeRecords(db, records);
  } finally {
    db.close();
  }
  return records.length;
}

/**
 * `mem build-store [--rig <name>] [--with-traces] [--store PATH]` — read the
 * WorkRecord spine from the dolt bead store (all rigs, or one `--rig`) and
 * persist it into the sidecar at `--store` (default `.mem/store.db`).
 *
 * With `--with-traces`, each record's transcript is resolved (P1.3) and parsed
 * for deterministic build/test/lint failure signatures (P1.6) before the write,
 * so the store carries the D8 signals the failure-triggered `ours` arm fires on.
 * With `--with-provenance`, each record's git baseline (repo + session-start
 * commit by date) is attached so it can be replayed as a git-checkout
 * environment, followed by its forward mirror `landed` (session-close branch tip
 * + survival), the work→landed-commit outcome signal for the direct-to-main
 * corpus. Without either flag, the store is the bead spine only (fast, no
 * `gc`/transcript/git IO).
 *
 * Rigs stream through the writer one at a time (read → attach → write per
 * rig), so peak memory is one rig's records, not the whole corpus. Each rig is
 * its own transaction; a failure mid-build aborts loudly and leaves a partial
 * store — fine for a rebuildable projection (re-run to rebuild).
 */
export async function buildStoreCommand(ctx: CommandContext): Promise<BuildStoreResult> {
  const rig = typeof ctx.options.rig === 'string' ? ctx.options.rig : null;
  const path = storePath(ctx.options);
  const withTraces = ctx.options['with-traces'] === true;
  const withProvenance = ctx.options['with-provenance'] === true;
  const joinPath =
    typeof ctx.options['session-join'] === 'string' ? ctx.options['session-join'] : null;
  const join: SessionJoin | null = joinPath === null ? null : loadSessionJoin(joinPath);
  const taskTypesPath =
    typeof ctx.options['task-types'] === 'string' ? ctx.options['task-types'] : null;
  // Mechanical typing (formula/structural) always runs; the artifact only
  // supplies the model-classified residue.
  const taskTypes: TaskTypeArtifact =
    taskTypesPath === null ? new Map<string, TaskTypeEntry>() : loadTaskTypes(taskTypesPath);
  // The artifact's session->path map resolves most residue sessions without
  // shelling `gc session logs` (~11 s/session); gc remains the fallback for
  // sessions the events stream never keyed.
  const resolve: SessionResolver | undefined =
    join !== null && join.sessionPaths.size > 0
      ? id => join.sessionPaths.get(id) ?? gcSessionResolver(id)
      : undefined;
  // Transcript-archive fallback (mem-h3di.4): with traces on, reaped transcripts
  // resolve to their durable restored copies. Default root is co-located with
  // the store; `--transcript-archive <dir>` overrides it. A missing/empty
  // archive is a silent no-op, so this is always safe to enable.
  const archiveRoot =
    typeof ctx.options['transcript-archive'] === 'string'
      ? ctx.options['transcript-archive']
      : defaultArchiveRoot(path);
  const archive: TranscriptArchive | undefined = withTraces
    ? loadTranscriptArchive(archiveRoot)
    : undefined;

  const run = doltRunner(defaultConnection());
  const rigs = rig === null ? await listRigs(run) : [rig];

  let count = 0;
  let recordsWithErrors = 0;
  let recordsWithRepo = 0;
  let recordsWithProvenance = 0;
  let recordsWithCommit = 0;
  let recordsWithCommitSha = 0;
  let recordsLanded = 0;
  let recordsMultiSession = 0;

  for (const name of rigs) {
    const spine = await readRig(run, name);
    // Canonical repo identity (mem-bme): pure name→name resolution, always on —
    // no IO, no flag. Runs before typing/traces so the resolved repo is present
    // for every downstream stage and the write.
    const located = attachRepo(spine);
    // The merged join attaches first: it pre-resolves each session's
    // transcript, so P1.3 only shells `gc` for the residue.
    const typed = attachTaskTypes(located, taskTypes);
    const joined = join === null ? typed : attachSessionJoin(typed, join);
    const traced = withTraces
      ? attachAndParse(joined, {
          ...(resolve && { resolve }),
          ...(archive && { archive }),
        })
      : joined;
    // Provenance reconstructs the session-start baseline; commit outcomes
    // recover the work→landing-commit (populating commit_sha) from the same
    // checkout; landed is its forward mirror (session-close branch tip +
    // survival). All three need the rig's git checkout, so they run together
    // behind --with-provenance, after the leak-safe session-start baseline.
    const records = withProvenance
      ? attachLanded(attachCommitOutcomes(attachProvenance(traced)))
      : traced;

    count += buildStoreFromRecords(path, records);
    recordsWithRepo += records.filter(r => r.repo !== undefined).length;
    recordsWithErrors += records.filter(r => (r.trace?.errors?.length ?? 0) > 0).length;
    recordsWithProvenance += records.filter(r => r.provenance !== undefined).length;
    recordsWithCommit += records.filter(
      r => r.provenance?.history_state === 'commit-by-date'
    ).length;
    recordsWithCommitSha += records.filter(r => r.outcome?.commit_sha !== undefined).length;
    recordsLanded += records.filter(r => r.landed?.landed_state === 'landed').length;
    recordsMultiSession += records.filter(
      r => r.agents.filter(a => a.suspect !== true).length >= 2
    ).length;
  }

  if (!ctx.options.json) {
    const scope = rig === null ? 'all rigs' : rig;
    const repoNote = `; ${recordsWithRepo}/${count} resolved a repo`;
    const traceNote = withTraces ? `; ${recordsWithErrors} carry trace errors` : '';
    const provNote = withProvenance
      ? `; ${recordsWithProvenance} carry a work_dir, ${recordsWithCommit} resolved a base commit, ${recordsWithCommitSha} resolved a landing commit, ${recordsLanded} landed on the branch`
      : '';
    const joinNote = join !== null ? `; ${recordsMultiSession} are multi-session` : '';
    console.error(
      `built ${count} work records from ${scope} into ${path}${repoNote}${traceNote}${provNote}${joinNote}`
    );
  }

  return {
    store: path,
    rig,
    count,
    records_with_repo: recordsWithRepo,
    records_with_errors: recordsWithErrors,
    records_with_provenance: recordsWithProvenance,
    records_with_commit: recordsWithCommit,
    records_with_commit_sha: recordsWithCommitSha,
    records_landed: recordsLanded,
    records_multi_session: recordsMultiSession,
  };
}
