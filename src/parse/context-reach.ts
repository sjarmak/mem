import {
  type ContentBlock,
  type TranscriptEntry,
  contentBlocks,
  resultBlockText,
} from './trace-parse.js';
import { normalizePath } from './recurrence.js';

/**
 * Deterministic **context-reach** extraction — every point in a transcript where
 * the agent reached for context it did not already hold: reading a file,
 * globbing or grepping for one, `bd show`-ing a bead, re-reading a closed issue,
 * paging a git log. Each such reach is the mechanical residue of a question the
 * agent actually had, and the set of them across a corpus is the demand side of
 * memory: what agents go looking for, how often, and whether the lookup paid.
 *
 * This module is the mechanical half only. It answers two questions, both by
 * table lookup over a known tool/command set, exactly as `matchRunner`
 * gates build/test/lint executions:
 *
 *   1. Did a reach happen? — the tool name is in {@link REACH_RULES} and, for
 *      Bash, {@link isContextCommand} holds: the command is lexed into its
 *      simple commands, every one of them is a read or a vouched-for filter, at
 *      least one is a read, and nothing in it writes.
 *   2. What was its mechanical target? — the value of the named argument, path-
 *      normalized when the argument is a path.
 *
 * **ZFC boundary.** It does NOT decide what the agent was *asking*, score a
 * reach's relevance, judge whether the reach was a good idea, or rank reaches
 * against each other. Turning `Read src/store/schema.ts` into the natural-
 * language question "what is the current store schema version?" is semantic
 * classification and belongs to a model lane downstream; it must not be
 * approximated here with keyword rules or thresholds. Nothing in this file may
 * inspect the *meaning* of the target string — only its shape as an argument.
 *
 * Pairing and skipping follow `trace-parse`'s established contract: a reach is
 * recorded when its `tool_result` is observed, so a transcript truncated
 * mid-call drops that call rather than reporting a fabricated empty result;
 * unparseable lines are skipped, as append-only logs may end mid-write.
 */

/** What kind of argument carried the reach's target. */
export type ReachKind = 'path' | 'query' | 'command';

/** One reach for prior context, paired with the result it got. */
export interface ContextReach {
  /** The transcript tool name, verbatim (`Read`, `Grep`, `Bash`, …). */
  tool: string;
  /** Whether the target is a filesystem path, a search query, or a shell command. */
  reach_kind: ReachKind;
  /** The reach's mechanical target: the argument's value, `normalizePath`d for
   * `path` reaches so it matches the store's normalized `file` projections. */
  target: string;
  /** The `tool_use` block id the reach was issued under — the pairing key, and a
   * stable within-transcript identifier for the reach. */
  tool_use_id: string;
  /** 0-based index, among `user` + `assistant` entries, of the turn that issued
   * the call (the same turn definition `TraceRun.n_turns` counts). */
  turn_index: number;
  /** The paired `tool_result`'s `is_error` flag — a reach that failed (missing
   * file, no matches) is still a reach, and the failure is signal. */
  result_is_error: boolean;
  /** Character count of the observed result text — a volume measure only; this
   * module never reads the text's content. */
  result_chars: number;
}

/** The result of scanning one transcript, carrying the denominator the reach
 * list alone cannot express: how many transcript entries actually parsed. Zero
 * entries means the input was not transcript JSONL at all, which callers must
 * treat as an error rather than as "this session reached for nothing". */
export interface ContextReachScan {
  entries_parsed: number;
  reaches: ContextReach[];
}

/**
 * A tool whose calls count as reaches, and which of its arguments carries the
 * target. `args` is tried in order and the first present, non-empty string wins
 * — the harness has spelled the same argument more than one way across versions
 * (`file_path` vs `path`), and an ordered list keeps both readable without a
 * second rule row.
 */
interface ReachRule {
  readonly tool: string;
  readonly args: readonly string[];
  readonly kind: ReachKind;
}

/**
 * Frozen tool→argument table. Read-only context retrieval only: `Edit`/`Write`
 * are writes, not reaches, and are deliberately absent. `Bash` is listed here
 * for its argument, but is additionally gated by
 * {@link CONTEXT_COMMAND_RULES} — most Bash calls build or mutate, and only the
 * retrieval-shaped ones are reaches.
 */
const REACH_RULES: readonly ReachRule[] = Object.freeze([
  { tool: 'Read', args: ['file_path', 'path'], kind: 'path' },
  { tool: 'NotebookRead', args: ['notebook_path', 'path'], kind: 'path' },
  { tool: 'Glob', args: ['pattern'], kind: 'query' },
  { tool: 'Grep', args: ['pattern'], kind: 'query' },
  { tool: 'Bash', args: ['command'], kind: 'command' },
] as const);

/** Index the rules by tool name once — the per-block lookup is a Map hit, not a
 * scan, because a transcript can carry tens of thousands of blocks. */
const REACH_RULE_BY_TOOL: ReadonlyMap<string, ReachRule> = new Map(
  REACH_RULES.map(rule => [rule.tool, rule])
);

/**
 * The opaque word a quoted span, a command substitution or a backslash escape
 * collapses to. It is a single non-word, non-space character, so it still reads
 * as "an argument is present" to a rule that requires one (`\S`), while matching
 * no rule's literal text. Quoted text is *data* — a commit message that quotes
 * `git log`, a `--notes` body, a grep pattern containing `>` — and collapsing it
 * is what stops that data from being read as a command.
 */
const OPAQUE_WORD = '\u0001';

/** Characters that end one simple command and begin the next: pipes, list
 * operators, sequencing, newlines and subshell parentheses. */
const SEGMENT_SEPARATORS = new Set(['|', ';', '&', '\n', '(', ')']);

/** One command string, split into the simple commands it actually runs. */
interface ShellScan {
  /** Each simple command, quoted spans and substitutions collapsed to
   * {@link OPAQUE_WORD}. Empty when `disqualified` is true. */
  readonly segments: readonly string[];
  /** A structurally mutating shape was seen outside quotes: a heredoc or
   * herestring (`<<`, `<<<`, `<<-`) or an output redirect (`>`, `>>`, `&>`,
   * `2>`). Either makes the command a write no matter what it reads. */
  readonly disqualified: boolean;
}

/** Index just past the quoted span opening at `start`. A single-quoted span ends
 * at the next quote; a double-quoted span honours backslash escapes. An
 * unterminated quote consumes the rest of the string. */
function skipQuoted(command: string, start: number): number {
  const quote = command[start];
  for (let i = start + 1; i < command.length; i += 1) {
    if (quote === '"' && command[i] === '\\') {
      i += 1;
      continue;
    }
    if (command[i] === quote) return i + 1;
  }
  return command.length;
}

/** Index just past the backquoted substitution opening at `start`. */
function skipBackquote(command: string, start: number): number {
  for (let i = start + 1; i < command.length; i += 1) {
    if (command[i] === '\\') {
      i += 1;
      continue;
    }
    if (command[i] === '`') return i + 1;
  }
  return command.length;
}

/** Index just past the `(`…`)` span opening at `open`, counting nesting and
 * skipping quoted spans so a parenthesis inside a string does not unbalance the
 * scan. An unbalanced span consumes the rest of the string. */
function skipParens(command: string, open: number): number {
  let depth = 0;
  for (let i = open; i < command.length; i += 1) {
    const ch = command[i];
    if (ch === "'" || ch === '"') {
      i = skipQuoted(command, i) - 1;
      continue;
    }
    if (ch === '(') depth += 1;
    else if (ch === ')') {
      depth -= 1;
      if (depth === 0) return i + 1;
    }
  }
  return command.length;
}

/**
 * Split a shell command into its simple commands, mechanically.
 *
 * This is lexing, not interpretation: it tracks quoting, command substitution
 * and the two redirect shapes, and splits on the separators above. It never
 * decides what a command *means* — that is the rule tables' job, and theirs is a
 * frozen lookup.
 *
 * Command substitutions collapse to {@link OPAQUE_WORD} rather than expanding
 * into segments, so `bd update x --notes "$(git log)"` is classified by its own
 * head (`bd update`, a write) and the `git log` inside it never registers as a
 * reach. A read command used as an argument to a mutating one is an argument,
 * not a reach.
 */
function scanShellSegments(command: string): ShellScan {
  const segments: string[] = [];
  let current = '';
  const flush = (): void => {
    const trimmed = current.trim();
    if (trimmed !== '') segments.push(trimmed);
    current = '';
  };

  for (let i = 0; i < command.length; ) {
    const ch = command[i];
    if (ch === '\\') {
      current += OPAQUE_WORD;
      i += 2;
      continue;
    }
    if (ch === "'" || ch === '"') {
      i = skipQuoted(command, i);
      current += OPAQUE_WORD;
      continue;
    }
    if (ch === '`') {
      i = skipBackquote(command, i);
      current += OPAQUE_WORD;
      continue;
    }
    if (ch === '$' && command[i + 1] === '(') {
      i = skipParens(command, i + 1);
      current += OPAQUE_WORD;
      continue;
    }
    // `<<`, `<<-` and `<<<` all introduce inline data the command consumes, and
    // in every observed spelling the consumer is a write (`cat <<EOF > f`,
    // `bd update --notes <<EOF`). A single `<` is an input redirect and reads.
    if (ch === '<' && command[i + 1] === '<') return { segments: [], disqualified: true };
    if (ch === '>') return { segments: [], disqualified: true };
    if (SEGMENT_SEPARATORS.has(ch)) {
      flush();
      i += 1;
      continue;
    }
    current += ch;
    i += 1;
  }
  flush();
  return { segments, disqualified: false };
}

/** A leading `NAME=value` environment assignment, which precedes the real
 * command word (`GIT_PAGER=cat git log`). */
const ENV_ASSIGNMENT = /^[A-Za-z_][A-Za-z0-9_]*=\S*\s+/;

/** A segment with its environment-assignment prefix removed, so every rule below
 * can anchor on the command word itself. */
function stageHead(segment: string): string {
  let head = segment;
  while (ENV_ASSIGNMENT.test(head)) head = head.replace(ENV_ASSIGNMENT, '');
  return head;
}

/**
 * Shell commands that retrieve prior context rather than building or mutating.
 * Mechanical token matching over a known command set — the same deterministic,
 * ZFC-clean device `runners.ts` uses to name a build runner, not meaning
 * detection: each entry names a specific read-only subcommand of a specific
 * tool, so nothing here depends on what the command's *arguments* mean.
 *
 * **Every rule anchors at `^`** and is matched against one segment's
 * {@link stageHead}, never against the raw command. Matching anywhere in the
 * string is what made `git commit -m "fix per git log"` and
 * `cat <<EOF > notes.md` read as reaches: the tokens were present, but not as
 * the command being run.
 *
 * No `g` flag: these are used with `.test()`, which is stateful only on global
 * regexes.
 */
const CONTEXT_COMMAND_RULES: readonly RegExp[] = Object.freeze([
  /^bd\s+(show|list|ready|blocked|history|dep\s+tree)\b/,
  /^git\s+(log|show|diff|blame)\b/,
  /^gh\s+(issue|pr)\s+view\b/,
  /^(rg|grep|ag)\s+\S/,
  /^(cat|head|tail)\s+\S/,
] as const);

/**
 * Stream filters that neither retrieve prior context nor mutate state. A
 * pipeline stage may be one of these without spoiling the reach — `git log |
 * head -n 3` is one reach — but a command built only from them is not a reach,
 * because nothing in it reached for anything.
 *
 * The list is an allowlist, and that is deliberate: an unrecognised stage
 * (`tee`, `xargs`, `sed`, a project script) disqualifies the whole command. A
 * downstream stage the table cannot vouch for may mutate, and this gate fails
 * closed rather than counting a build pipeline as a lookup.
 */
const READ_FILTER_RULES: readonly RegExp[] = Object.freeze([
  /^(cat|head|tail|sort|uniq|wc|cut|tr|nl|rev|fold|column|jq|cd|pwd)\b/,
] as const);

/**
 * Invocations whose command word is on one of the allowlists above but whose
 * flags make that call write a file. Checked first, so an allowlisted head can
 * never launder a write past the gate.
 */
const MUTATING_INVOCATION_RULES: readonly RegExp[] = Object.freeze([
  // `sort -o FILE` / `sort --output=FILE` writes FILE.
  /^sort\b.*\s--?o(utput)?[\s=]/,
  // `uniq INPUT OUTPUT` — two non-flag operands — writes OUTPUT.
  /^uniq(\s+-\S+)*\s+\S+\s+[^-\s]/,
] as const);

/**
 * True when a shell command is a retrieval of prior context: it runs at least
 * one read command, every stage it runs is a read or a vouched-for filter, and
 * it carries neither a heredoc nor an output redirect.
 */
function isContextCommand(command: string): boolean {
  const { segments, disqualified } = scanShellSegments(command);
  if (disqualified) return false;

  let reached = false;
  for (const segment of segments) {
    const head = stageHead(segment);
    if (MUTATING_INVOCATION_RULES.some(re => re.test(head))) return false;
    if (CONTEXT_COMMAND_RULES.some(re => re.test(head))) {
      reached = true;
      continue;
    }
    if (READ_FILTER_RULES.some(re => re.test(head))) continue;
    return false;
  }
  return reached;
}

/** The first argument in `args` that is present as a non-empty string, or null
 * when the call carried none — a tool_use with no readable target is not a
 * usable reach and is skipped rather than recorded with an empty target. */
function ruleTarget(rule: ReachRule, input: Record<string, unknown> | undefined): string | null {
  if (!input) return null;
  for (const arg of rule.args) {
    const value = input[arg];
    if (typeof value === 'string' && value.trim() !== '') return value.trim();
  }
  return null;
}

/** A reach recorded at call time, awaiting its `tool_result`. */
interface PendingReach {
  tool: string;
  reach_kind: ReachKind;
  target: string;
  turn_index: number;
}

/** Project a `tool_use` block into a pending reach, or null when the tool is not
 * a reach tool, the Bash command is not retrieval-shaped, or no target argument
 * is present. */
function pendingReachOf(block: ContentBlock, turn_index: number): PendingReach | null {
  if (!block.name) return null;
  const rule = REACH_RULE_BY_TOOL.get(block.name);
  if (!rule) return null;

  const target = ruleTarget(rule, block.input);
  if (target === null) return null;
  if (rule.kind === 'command' && !isContextCommand(target)) return null;

  return {
    tool: rule.tool,
    reach_kind: rule.kind,
    target: rule.kind === 'path' ? normalizePath(target) : target,
    turn_index,
  };
}

/**
 * The result text observed for a reach, as a character count only. The
 * structured `toolUseResult` is preferred when the harness wrote one (a plain
 * string for file reads, `stdout`/`stderr` for Bash), falling back to the
 * `tool_result` block's own content. Never inspected — only measured.
 */
function reachResultText(entry: TranscriptEntry, block: ContentBlock): string {
  const tur = entry.toolUseResult;
  if (typeof tur === 'string') return tur;
  if (tur !== null && typeof tur === 'object') {
    const shaped = tur as { stdout?: unknown; stderr?: unknown };
    if (typeof shaped.stdout === 'string' || typeof shaped.stderr === 'string') {
      const stdout = typeof shaped.stdout === 'string' ? shaped.stdout : '';
      const stderr = typeof shaped.stderr === 'string' ? shaped.stderr : '';
      return `${stdout}\n${stderr}`;
    }
  }
  return resultBlockText(block.content);
}

/**
 * Scan a transcript's JSONL — full text or a line iterable — for context
 * reaches, reporting the parsed-entry denominator alongside them.
 *
 * Reaches come back in the order their results were observed. A `tool_use`
 * whose result never appears (a transcript truncated mid-call) is dropped, the
 * same way `parseTranscript` drops an unpaired Bash execution, so no row ever
 * reports a result that was not seen.
 */
export function scanContextReaches(input: string | Iterable<string>): ContextReachScan {
  const lines = typeof input === 'string' ? input.split('\n') : input;
  const pending = new Map<string, PendingReach>();
  const reaches: ContextReach[] = [];
  let entries_parsed = 0;
  let turn_index = -1;

  for (const line of lines) {
    if (line.trim() === '') continue;

    let entry: TranscriptEntry;
    try {
      entry = JSON.parse(line) as TranscriptEntry;
    } catch {
      continue;
    }
    entries_parsed += 1;

    if (entry.type !== 'user' && entry.type !== 'assistant') continue;
    turn_index += 1;

    if (entry.type === 'assistant') {
      for (const block of contentBlocks(entry)) {
        if (block.type !== 'tool_use' || !block.id) continue;
        const reach = pendingReachOf(block, turn_index);
        if (reach) pending.set(block.id, reach);
      }
      continue;
    }

    for (const block of contentBlocks(entry)) {
      if (block.type !== 'tool_result' || !block.tool_use_id) continue;
      const reach = pending.get(block.tool_use_id);
      if (!reach) continue;
      pending.delete(block.tool_use_id);
      reaches.push({
        tool: reach.tool,
        reach_kind: reach.reach_kind,
        target: reach.target,
        tool_use_id: block.tool_use_id,
        turn_index: reach.turn_index,
        result_is_error: block.is_error === true,
        result_chars: reachResultText(entry, block).length,
      });
    }
  }

  return { entries_parsed, reaches };
}

/**
 * Every context reach in a transcript, in result order. The thin public form of
 * {@link scanContextReaches} for callers that already know the input is a
 * transcript; anything that must distinguish "no reaches" from "not a
 * transcript" needs the scan's `entries_parsed`.
 */
export function extractContextReaches(input: string | Iterable<string>): ContextReach[] {
  return scanContextReaches(input).reaches;
}

/** Reach counts keyed by tool name, for reporting. Pure tally, no judgment. */
export function countReachesByTool(reaches: readonly ContextReach[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const reach of reaches) counts[reach.tool] = (counts[reach.tool] ?? 0) + 1;
  return counts;
}
