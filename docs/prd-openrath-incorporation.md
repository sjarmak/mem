# PRD — Incorporating OpenRath (arXiv 2606.19409) into mem

> Supersedes and extends [research-openrath-2606.19409-incorporation.md](./research-openrath-2606.19409-incorporation.md).
> Status: risk-annotated PRD. Solo-dev project — land direct to main, no PR ceremony. Result numbers HELD under publication-freeze.

## TL;DR

OpenRath makes agent runtime state — transcripts, tool evidence, sandbox placement, **lineage/branch provenance, token usage, replay info, and memory-event records** — a first-class composable `Session` value, with fork/merge/replay as explicit runtime ops "rather than states reconstructed from external traces." mem's substrate is the *post-hoc dual*: the same lineage reconstructed after the fact from city exhaust. We adopt OpenRath at **exactly one boundary** — a one-direction **projecting adapter** that reads a Session and emits ONLY mem's existing field-separated types (provenance `cut` events, `MemoryEvent` records, a `QueryWork`/`WorkRecord` with named join keys). The Session is **never persisted or carried forward**. We do **not** ingest whole Sessions as benchmark input (that leaks the outcome label into the input and destroys validity), we do **not** re-platform Gas City orchestration onto OpenRath `Workflow`/`Selector`, and we do **not** let mem own a brains/orchestration rewrite. OpenRath's real, narrow win is filling the two keys mem *provably cannot* reconstruct (`commit` across the squash-wall, `used` retrieval causality) and replacing date-guessed `base_commit` with runtime-authoritative lineage.

## Problem & Opportunity

**The reconstruction tax.** mem re-derives join keys the city never handed it cleanly: true fork-point / base-SHA via the `capture-provenance.sh` merge-base hook and `ingest-SHA-capture` (mem-75t.15); ~89% null `repo`; `convoy/pr ~0` populated; `record_links` empty; `src/ingest/provenance.ts` `deriveProvenance` produces a *date-guessed* base commit (`history_state` = `commit-by-date` or `unresolved`, never `recorded` — confirmed at `provenance.ts:237`). OpenRath would emit these natively as runtime truth (`history_state='recorded'`).

**The memory-effectiveness gap.** OpenRath's authors *explicitly defer* memory effectiveness and quantitative evaluation to future work. That deferred question — does retained/retrieved memory measurably improve success / iterations / cost — is mem's headline. Positioning: OpenRath = capture/runtime layer; mem = the empirical memory-effectiveness benchmark over real outcomes. They compose; they don't compete. mem's differentiated asset is an *uncontaminated, independently-scored* lift number; protecting it is the priority, not OpenRath adoption itself.

**Crucial reframe.** mem's post-hoc reconstruction is NOT pure waste — the derivation is *what manufactures the field separation* (label-free `started`/cut fields kept separate from outcome `pr`/`commit_sha`/`base_commit`). OpenRath doesn't remove the tax; it converts it from "reconstruct the keys" to "project + re-guard," which is strictly cheaper and equally safe **only if the firewall covers every projected field**.

## Goals / Non-Goals

### Goals
- Adopt OpenRath at the **capture layer only**: a pure projecting adapter Session → (provenance `cut` events, `MemoryEvent`, `QueryWork`/`WorkRecord`).
- Fill the keys mem provably cannot reconstruct: `commit` (squash-wall gap) and `used` (retrieval causality — mem's `used` edge is empty by construction).
- Replace date-guessed `base_commit` with runtime-authoritative `history_state='recorded'`, with the merge-base hook as fallback and OpenRath overriding when present.
- Make the validity firewall **executable and load-bearing before any adapter ships** (leak-injection contract test, TDD red-first).
- Standardize `~/brains` write/recall onto the OpenRath `memory_event` shape so brains retrieval becomes observable, and admit brains as a candidate `ours` `MemorySystem` (object under test, never input plumbing).

### Non-Goals
- **Do NOT feed whole Sessions as benchmark input — anywhere (store / retrieval corpus / task construction) — without a leakage firewall.** A Session bundles memory-events + replay + outcome lineage into one value that erases field provenance; ingesting it whole collapses "input/label separated by construction" into "we trained on the labels."
- **mem does not own brains or orchestration rewrites.** No re-platforming convoy/DAG/warm-fork onto OpenRath `Workflow`/`Selector` (YAGNI — the city's bespoke mechanisms work, are solo-dev-owned, and typing convoy as a `Selector` before a second routing strategy exists is a lonely-interface/single-entry-registry slop pattern).
- Do NOT relax the leak scan on `base_commit`/`commit_sha` because the data is now runtime-authoritative — "better data" does not change which fields are leak identifiers.
- Do NOT pursue paid APIs for any eval lane (scix no-paid-API; free/local models only).
- Do NOT release lift numbers under publication-freeze (diagnostics/agreement-rates are operational health, not headline lift — pending Stephanie confirmation, see Open Decisions).

## Proposed Incorporation (per target)

| Target | Adopt | Boundary / guard |
|---|---|---|
| **provenance tracking** | Runtime-authoritative fork/merge lineage projected into `cut` events (`source=openrath-runtime`, `history_state='recorded'`); third producer behind the `capture-provenance.sh` / `liveRef.ts` R3 merge-base gate, OpenRath overriding when present. | The projecting adapter is the ONLY path from city runtime into the mem store. `base_commit`/`commit_sha` stay label-side leak identifiers regardless of `history_state`. |
| **mem benchmark** | OpenRath `replay` *only as a Lane-B environment-rehydration backend* (re-provision sandbox/repo at `started`), addressing the lvp.8/lvp.19 executable-env wall. | Lane A static replay (`membench/replay.py`) already exists under `loo_bounded`+`assert_no_leak` — OpenRath adds nothing and would HARM it. Lane B is GATED (Stephanie eval-design). Every Session routes through `loo_bounded` + extended `assert_no_leak` (strips/raises on `replay_info`+`outcome_lineage`) before any arm sees it. |
| **~/brains** | `brains` EMITS the standardized `memory_event` shape (write → `WorkRef closed_at`; recall → retrieval `MemoryEvent`), making retrieval observable; enters as a candidate `ours` `MemorySystem` via the `ours_system.py` adapter pattern, tagged `source=brains`. | Object under test, NEVER input plumbing. Dual-store hazard: `~/brains` is separate from `.mem/store-v9.db`; its join keys must come from runtime-authoritative lineage or the reconstruction tax reappears inside brains. brains data for a bead with no runtime lineage is DROPPED from scoring (fail-closed), never back-filled from mem's reconstructed keys. |
| **Gas City orchestration** | ONLY the typed first-class **lineage value** that fork/merge emits (natively populates convoy carving + provenance edges mem hand-derives). | Leave control flow on the city's bespoke mechanisms (`bbon/narrative_diff.py` warm-fork, convoy/DAG + polecat dispatch). City is the PRODUCER, gated UPSTREAM under the 29-rule/ZFC PR-gated regime — the LARGEST dependency, NOT solo-dev-mergeable. Gate the city investment on the Phase-1 agreement-rate number. |

## Phased Plan

Phases 0–2 deliver standalone value with **zero** Gas-City-producer and zero Phase-N dependency. Each is solo-dev-mergeable and emits diagnostics, not result numbers.

### Phase 0 — Leak-injection contract test (TDD, failing-first)
- **Goal:** Make the firewall contract EXECUTABLE before any adapter ships. Construct a synthetic OpenRath-shaped `Session` dict carrying a sentinel outcome value (`commit_sha = 'SENTINELLEAK0000'`) in its lineage AND a sentinel memory-event record. Assert: (a) a `Session→WorkRecord` projection routes the sentinel `commit_sha` ONLY into `record['outcome']['commit_sha']` and DROPS the memory-event payload; (b) feeding the projected record through the ladder adapter RAISES `OutcomeLeakError` if the sentinel reaches any agent-readable file (`instruction.md`/`task.toml`). Add a **positive-path** assertion: the adapter SUCCESSFULLY projects a known-good `fork_point` into a `cut` event and a known-good memory-event into a `MemoryEvent` (a rejection-only test cannot distinguish "rejected a leak" from "failed to parse"). Add an **allow-list** assertion: a Session with a novel sentinel field makes the projector RAISE (not silently drop).
- **Entry:** `validity.py` (`loo_bounded`/`assert_no_leak`/`LeakageError` @ lines 64/117/127) and `grading/leak_guard.py` (`OutcomeLeakError` @ 27, `_IDENTIFYING_KEYS=('pr','commit_sha','base_commit')` @ 24) confirmed on HEAD. No adapter exists.
- **Exit:** Test in `memory-bench/tests` FAILS today (no adapter) — correct TDD red. Firewall contract is a written, runnable spec. Surfaces whether `_IDENTIFYING_KEYS` must extend for memory-event records before Phase 2. No result number.

### Phase 1 — Pure projecting adapter + reconstruction-tax diagnostic
- **Goal:** Write `memory-bench/membench/openrath_adapter.py` (mirroring `deriveProvenance`): one function `Session → (list[cut events, source=openrath-runtime, history_state='recorded'], list[MemoryEvent], QueryWork)`. **ALLOW-LIST, not deny-list:** emit ONLY enumerated leak-safe kinds (`cut` from `fork_point` as a SHA-guarded 40-hex git-sha, plus `claim`/`suspend`/`resume`/`handoff`); EXCLUDE `used`/`replay` (Phase 2); RAISE on any unrecognized Session field. Run offline on closed beads that ALSO have a `capture-provenance.sh` merge-base record; diff runtime fork SHA vs reconstructed merge-base. Round-trip the projection through `loo_bounded` + `assert_no_leak`.
- **Entry:** Phase 0 test merged (red). Sample OpenRath-shaped Sessions available offline; `.mem/store-v9.db` readable. No runtime adoption, no new substrate, no paid API.
- **Exit:** Adapter turns the Phase-0 test green for the leak-safe subset. Produces the **agreement-rate diagnostic**: runtime-fork-SHA vs hook-merge-base match rate (directly tests the 97% squash-wall finding) — write BOTH, tag source, NEVER collapse to one; disagreement is a data signal, not a fallback to silence. **HALT condition:** the runtime fork-point MUST come from a source INDEPENDENT of the merge-base hook (e.g. claim-time HEAD recorded at claim, not the date-derived base). If no independent source exists, mark agreement-rate UNMEASURABLE and HALT before Phase 1.5 — do not merge an adapter whose payoff number is self-referential. Solo-dev-mergeable; emits a diagnostic.

### Phase 1.5 — v9 `provenance_events` sink merge + adapter wiring
- **Goal:** Merge `feat/provenance-event-log` (v9 `provenance_events`, NOT yet on HEAD — confirmed) to HEAD, re-pin `~/.mem-cli` after the schema change (warm cold `bd` with one direct call per the pinning gotcha), and wire the adapter to emit via `mem provenance record --source openrath-runtime`. Add the runtime `cut` as a THIRD producer behind the existing merge-base gate, OpenRath overriding, hook as fallback. **Decouple** the independently-useful `provenance_events` merge from the OpenRath override branch: keep the dual-source override behind a feature flag defaulting OFF, flipped only after `source=openrath-runtime` has fired on real (non-fixture) data — so the override is never dormant-but-maintained.
- **Entry:** Phase 1 adapter green and diagnostic landed with a non-trivial, non-self-referential saving. City-producer go/no-go decision made FIRST (see Open Decisions).
- **Exit:** `provenance_events` log on HEAD; `~/.mem-cli` re-pinned and verified. Adapter writes recorded fork-point events dedup'd on a deterministic id. 89%-null-repo / convoy~0 / empty-`record_links` floor measurably lifted from runtime truth for beads with OpenRath data. Still capture-only; no Session field beyond named join keys touches input. Standing observability metric: count of production `cut` events with `source=openrath-runtime` (zero for a defined window ⇒ erosion alert, adapter is dead weight).

### Phase 2 — Firewall extension + brains-as-object-under-test capture
- **Goal:** EXTEND the firewall for **label-side-until-proven** signal. (a) Make memory-event records and brains recall events pass through `loo_bounded` keyed on the **recalled item's closed time** (NOT the recall-event timestamp — that is itself a weak label side-channel). (b) Add a sibling-check extension for recall-provenance links: brains linking a lesson to a convoy/PR is a same-work signal `is_sibling` (`validity.py:96`, fires only on `convoy_id`/`pr`/`external_ref`) does not yet catch. (c) Standardize brains write/recall onto the `memory_event` shape via a thin `brains→MemoryEvent` emitter. (d) Add the `used` edge (project ONLY `A-used-B`, never `B-outcome`). brains enters as a candidate `ours` `MemorySystem`, tagged `source=brains`. Adopt the **conservative default in code**: all memory-event payload is label-side; only the `loo_bounded`-gated recall TARGET is input.
- **Entry:** Phase 1.5 landed. Phase-0 test extended with a sentinel LOW-ENTROPY memory-event leak case that currently passes through (proving the gap), driving the firewall extension red→green.
- **Exit:** `loo_bounded` admits recall/memory-events ONLY through the temporal+sibling door; extended leak-injection test green for memory-event records. brains retrieval observable (`used` no longer empty) WITHOUT any recall content reaching input outside the cut. Non-circularity test asserts the recall DECISION and the success measurement derive from disjoint observation sources with no shared timestamp/key provenance. Permutation control: shuffle recall-event timestamps; if lift survives the shuffle, the recall channel is leaking and the run is void. Capped/batched recall ingest (no per-step GC blowup).

### Phase N — Lane-B live re-execution backend (GATED)
- **Goal:** Use OpenRath native replay ONLY as a Lane-B ENVIRONMENT-rehydration backend for the with-vs-without-memory ablation — re-provision the sandbox/repo at `started` instead of reconstructing an executable env (addresses the lvp.8/lvp.19 wall). Rehydrate the environment from the fork-point **commit tree** (deterministic checkout at `base_commit`) rather than a runtime sandbox snapshot, so env state cannot carry forward post-cut artifacts (build cache, partial fixes, fixture files). Fingerprint-diff fails if the rehydrated tree differs from a clean checkout at `base_commit`. Seed transcript/tool-evidence ONLY post-cut (replaying the exact tool sequence reconstructs the answer). Every Session routes through `loo_bounded(corpus, query)` and `assert_no_leak` EXTENDED to strip/raise on `replay_info`+`outcome_lineage` before ANY arm sees it. Free/local models only.
- **Entry:** Phases 0–2 landed; firewall proven to reject `replay_info`/`outcome_lineage` by injection test; a runtime-lineage source (city producer or a stand-in env) available; **explicit Stephanie eval-design GO recorded in a tracked artifact**.
- **Exit:** Lane-B ablation runs with environment rehydrated from fork-point, transcript/tool-evidence seeded ONLY post-cut, zero outcome/replay-determinism leakage caught by the extended guard, determinism-injection test green. Output is labeled a COUNTERFACTUAL re-run under local-model substitution (a typed schema field, NOT silently promotable to ground-truth) — a local-model swap changes the trajectory. Numbers HELD under publication-freeze.

## The Validity Firewall

This is the load-bearing section. mem's benchmark validity is enforced by **field provenance**: the schema keeps label-free fields (`started` = the D6 strict cut) separate from outcome fields (`pr`/`commit_sha`/`base_commit`), scanned out by two firewalls. OpenRath's `Session` is the exact inversion — one composable value that erases field provenance. The "capture vs input" framing of the grounding doc is REPLACED by the sharper line: **projected back into field-separated columns (safe) vs travels whole (fatal).**

### What may enter eval input
- Provenance `cut`/fork-point events (label-free join keys: `started`, fork SHA), via the allow-list projector.
- A recalled brains/memory item, **only** through `loo_bounded`: its `closed` time is strictly before `query.started`, it is not a sibling (convoy/pr/external_ref **and** recall-provenance link), and it is keyed on the recalled item's `closed` time — NOT the recall-event timestamp.
- The `used` edge as `A-used-B` only (never `B-outcome`).

### What may NOT enter eval input
- Any whole `Session` value.
- `replay_info`, `outcome_lineage`, `commit_sha`, `base_commit`, `pr` — label-side regardless of `history_state` (`recorded` does not relax the scan).
- Memory-event record PAYLOAD, replay deltas, token-usage curves, winning-branch selector state — **outcome-correlated low-entropy signal a substring scan structurally cannot catch.** Default: label-side-until-proven; per-field relaxation is a SEMANTIC judgment (ZFC: belongs to a model/human, never an arm-author regex under deadline) that fails the injection test by default.
- The recall-event timestamp (a weak label side-channel — the system recalled it *because* it was relevant to the outcome).

### Assertions that enforce it
1. **Allow-list projector** (`openrath_adapter.py`): emits ONLY enumerated leak-safe kinds; RAISES on any unrecognized Session field. Net-positive routing branches are an erosion smell — run `/slop-check` on every extension.
2. **`loo_bounded`** (`validity.py:117`): temporal cut (`closed < started`) + self/chain/sibling exclusion. The ONLY door to the corpus for an arm. Extended in Phase 2: `is_sibling` fires on recall-provenance convoy/PR links.
3. **`assert_no_outcome_leak`** (`leak_guard.py:46`): case-insensitive substring scan over agent-readable surfaces. Extended in Phase N to strip/raise on `replay_info`+`outcome_lineage`. Test asserts no code path branches the scan on `history_state=='recorded'`, and that the scan runs over STRUCTURED provenance metadata reaching any agent-readable surface, not only free-text files.
4. **Empirical leak detector (backstop):** a held-out-label correlation gate over the admitted input set that FAILS the run if any admitted field's mutual information with the outcome label exceeds a calibrated threshold (mechanical/ZFC-clean — measures correlation, does not judge admissibility). Catches real-valued leaks the substring scan structurally cannot.
5. **Standing strip-the-arm null check (CI gate):** for every memory arm, auto-run the no-memory control; FAIL if the lift does not collapse to within noise. A surviving lift under arm-strip is the definitive contamination signal and BLOCKS any result, diagnostic or headline.

## Risk Register

Consolidated and de-duped from all three premortems, ranked by likelihood × impact (H=3, M=2, L=1).

| # | Risk | L | I | Score | Mitigation |
|---|---|---|---|---|---|
| 1 | **Solo-dev mem effort absorbed into the upstream Gas City producer** (29-rule PR-gated, largest/lowest-immediate-benefit dependency); a material-looking ~40% agreement diagnostic greenlights the city investment and mem becomes a part-time gascity contributor instead of running evals. | H | H | 9 | Hard, written time-box on city-producer work, not a go/no-go. ALL of Phases 0–2 stay runnable against SAMPLE Sessions + merge-base-hook fallback with ZERO gascity dependency. Defer the city producer until a memory-effectiveness lift exists and is bottlenecked on provenance quality — the hook fallback already pays most of the tax. |
| 2 | **Projector field-routing erosion:** `openrath_adapter.py` extended across phases; a later field (`winning_branch`, `replay.determinism_seed`) routed to a column no firewall scans; the Phase-0 sentinel test still passes (it tests only Phase-0 fields). Firewall bypassed by a field it was never told about. | H | H | 9 | ALLOW-LIST not deny-list: RAISE on any unrecognized Session field. Contract test with a novel sentinel field asserts the projector raises (not drops-silently). `/slop-check` on every adapter extension per the erosion-review trigger. |
| 3 | **`leak_guard` 3-key substring scan blind to outcome-correlated low-entropy signal** (memory-event payload, replay deltas, token curves, selector state); an arm author relaxes the conservative default to recover discarded signal; no test fails because the guard knows 3 string IDs. | H | H | 9 | Empirical held-out-label correlation gate alongside the substring scan (Firewall #4). Conservative default permanent in code; any per-field relaxation routed through explicit human/model adjudication that fails the injection test by default — never an arm-author call under deadline. |
| 4 | **Phase-1 reconstruction-tax diagnostic is a tautology** when no real producer exists: fixtures shaped from the merge-base hook compared against that hook → ~100% agreement that proves nothing; the SCOPE/COST gate fires on a non-number; adapter ships and is maintained with no demonstrated saving. | H | H | 9 | Phase-1 exit REQUIRES the runtime fork-point from a source INDEPENDENT of the merge-base hook (claim-time HEAD vs date-derived base) on ≥N real closed beads. If no independent source, mark UNMEASURABLE and HALT before Phase 1.5. Adapter existence gated on a non-self-referential disagreement signal. |
| 5 | **Adapter rots against an arXiv-stage Session schema that drifts** (`fork_point→lineage.origin`, `memory_event_records` split, replay gains fields); adapter silently no-ops on renamed fields — indistinguishable from correct "drop the payload" firewall behavior — so the green contract test masks a dead adapter. | H | M | 6 | Positive-path assertion in the Phase-0 test (successfully projects known-good fork_point + memory-event), not only sentinel rejection. Pin the adapter to an exact schema version with a HARD validation error (not silent skip) on any unrecognized top-level key. Version fixtures against a captured real Session sample, not the paper's prose. |
| 6 | **Dual-source override is a lonely interface** the OpenRath branch never takes in production (Sessions appear only in tests); dual-source merge logic + adapter + `~/.mem-cli` re-pin gotcha all maintained, exercised solely by fixtures. | H | M | 6 | Do NOT land the override in Phase 1.5. Keep the adapter a standalone offline diagnostic until a real producer emits a non-fixture Session. Feature-flag the dual-source merge OFF by default, flipped only after `source=openrath-runtime` fires on real data. Track production `source=openrath-runtime` count; zero for the window ⇒ delete, don't maintain. |
| 7 | **leak_guard / human-discipline default eroded under publication pressure:** an arm author decides a memory-event field "is just provenance now"; the `input/label separated by construction` claim collapses into "trained on a low-entropy shadow of the label." | M | H | 6 | Phase-2 sentinel low-entropy leak case stays red until admitted only through `loo_bounded`. Permanent guard + comment at `_IDENTIFYING_KEYS`. Conservative default permanent; relaxation fails the injection test by default. |
| 8 | **mem and ~/brains co-evolve across the dual-store boundary:** reconstruction tax reappears inside brains; recall-decision data couples to mem's success signal; scoring becomes circular; mem stops being an independent benchmark OVER brains. | M | H | 6 | brains enters ONLY via `ours_system.py`, tagged `source=brains`; recall/write events join exclusively on runtime-authoritative lineage keys (never mem's reconstructed keys, never round-trip). Non-circularity test (disjoint observation sources). Bead with no runtime lineage ⇒ brains data DROPPED from scoring (fail-closed). |
| 9 | **Recall-event side-channel:** recall-provenance link `is_sibling` doesn't catch, OR the recall SELECTION is outcome-correlated even keyed on closed-time. Scoring circular. | M | H | 6 | Ship the Phase-2 `is_sibling` extension (failing test first) BEFORE any brains recall reaches input. Permutation control: shuffle recall-event timestamps; lift surviving the shuffle ⇒ recall channel leaking, run void. |
| 10 | **base_commit dual-role regression:** once runtime-authoritative, an arm author treats it as "just provenance," exposes it in task-construction or drops it from the scan; it remains the LOO join key = the answer in a fail-to-pass task, traveling as structured metadata that never hits the free-text scan. | M | H | 6 | Permanent guard comment + executable invariant: test asserts `base_commit`/`commit_sha` stay in `_IDENTIFYING_KEYS` regardless of `history_state`, scan runs over structured metadata on any agent-readable surface, and no code path branches the scan on `history_state=='recorded'`. |
| 11 | **Phase N built/run despite the gate**, then environment-only rehydration erodes (post-cut tool seeding creeps in to stabilize flaky local-model runs), reconstructing the answer; the counterfactual lift reproduces the lvp.8/lvp.19 wall in a new costume and gets cited as a real result under freeze. | M | H | 6 | Hard-block Phase N behind a tracked Stephanie GO. Rehydrate from the fork-point COMMIT TREE (deterministic checkout at `base_commit`) + fingerprint-diff vs clean checkout, not a runtime snapshot. Determinism-injection test proving transcript/tool-evidence seeded ONLY post-cut. Label every Lane-B number a COUNTERFACTUAL in the schema itself. |
| 12 | **Lane-B sandbox carries the answer:** a runtime snapshot at `started` includes build cache / partial fixes / fixture files encoding the outcome, below any field-scanning firewall. | M | H | 6 | (Folded into #11) Rehydrate from version-controlled source only (commit tree at `base_commit`); fingerprint-diff fails on any drift from a clean checkout. |
| 13 | **Diagnostics give false assurance:** Phase 0–1.5 agreement-rate diagnostics look clean (they measure fork-SHA vs merge-base, never "does an admitted field correlate with the held-out label"), so boundary erosion goes unnoticed until an external reviewer's strip-the-arm ablation survives. | M | H | 6 | Make strip-the-arm a STANDING CI gate (Firewall #5), not an external reviewer's job. Emit agreement-rate + production `source=openrath-runtime` count as standing observability metrics; "producer event count = 0 for 30 days" is an explicit erosion alert. Distinguish "rejected a leak" from "failed to parse" at the adapter entry. |
| 14 | **"mem = the benchmark OpenRath defers" positioning collapses:** six months in, every mem lift is HELD, contaminated, or a local-model counterfactual, while OpenRath ships its own eval appendix — no clean answer to "what did mem measure that OpenRath didn't?" | M | H | 6 | Protect the differentiated asset: schedule the uncontaminated with-vs-without-memory ablation over the EXISTING work-audit graph (Lane A `membench/replay.py` — OpenRath adds nothing, would HARM it) as priority, INDEPENDENT of all OpenRath adoption. OpenRath capture is a tax-reducer, never a prerequisite. |
| 15 | **YAGNI boundary erodes:** once gascity emits Session lineage, "just route it through the typed Selector" looks free; a single-entry registry / lonely interface gets built and maintained on both sides of a PR-gated boundary. | L | M | 2 | Decision explicit in the adapter module docstring: take ONLY the typed lineage VALUE; leave control flow on bespoke city mechanisms. `/slop-check` rejects any `Selector`/`Workflow` type with one implementer or a registry with one entry until a SECOND routing strategy demonstrably exists. |
| 16 | **Numbers released despite freeze** because the diagnostic/lift boundary is assumed, not confirmed. | L | M | 2 | Explicit written Stephanie ruling on the freeze boundary before Phase N. Tag every emitted artifact at creation as DIAGNOSTIC or RESULT in code; publication gate is mechanical. Default HELD when unclassified. |
| 17 | **Lane-B value held hostage to OpenRath:** the lvp.8/lvp.19 env-wall fix never lands because Phase N is gated on both Stephanie and the absent city producer. | M | L | 2 | Decouple env-rehydration from OpenRath: re-provision a sandbox/repo at `started` from ANY runtime fork-point source (existing `capture-provenance.sh` claim-time HEAD), as a separate track. Keep Phase N labeled GATED; its absence must not retroactively justify maintaining the capture adapter. |

## Open Decisions for Stephanie

These are eval-design / scope calls only she can make. Phases 0–2 do NOT depend on any of them; only Phase N and the city-producer investment do.

1. **EVAL-DESIGN — Is Lane-B live re-execution (Phase N) worth building at all?** Local-model substitution makes any "replay" a counterfactual re-run, and the substitution itself may invalidate the replay (different trajectory). The firewall question is RESOLVED (route every Session through `loo_bounded` + extended `assert_no_leak`); the open question is whether a free/local-only counterfactual produces a defensible lift signal or just reproduces the lvp.8/lvp.19 validity wall in a new costume.
2. **SCOPE/COST — Invest in the Gas-City-side Session producer now, or run indefinitely on the capture adapter against sample Sessions + the merge-base-hook fallback?** Upstream, PR-gated under the 29-rule/ZFC regime; the largest dependency and lowest immediate mem benefit. *Recommendation:* gate the city investment on the Phase-1 agreement-rate number being non-trivial AND non-self-referential — and even then time-box it (Risk #1).
3. **FIREWALL-EXTENSION SUFFICIENCY — Who adjudicates per-field memory-event admissibility, and do we accept the conservative default permanently?** Proving a given memory-event field admissible is a SEMANTIC per-field judgment (ZFC: a model/human, not a regex). Default proposal: "all memory-event payload is label-side; only the `loo_bounded`-gated recall TARGET is input" — safe but throws away signal.
4. **base_commit DUAL-ROLE — Confirm it stays label-side permanently** despite runtime-authoritative provenance sharpening the temptation to relax. Resolved as a DECISION (keep it label-side) but flagged as a standing erosion risk worth a permanent comment/guard, not a one-time fix.
5. **PUBLICATION-FREEZE BOUNDARY — Does the freeze cover OpenRath-derived diagnostics (agreement rates), or only headline lift numbers?** Assumed the latter (diagnostics OK, lift HELD). Confirm before Phase N runs.

## Recommended First Step

Build the **thin Session-lineage → WorkRecord adapter, validated against reconstructed keys on closed beads** — but lead with the Phase-0 contract test so the firewall is executable before the adapter exists.

Concretely:
1. **Write `memory-bench/tests/test_openrath_leak_injection.py`** (TDD red): a synthetic `Session` dict with `fork_point` (40-hex), a `memory_event_records` entry, and `outcome.commit_sha='SENTINELLEAK0000'`. Assert (a) projection routes the sentinel ONLY to `record['outcome']['commit_sha']` and DROPS the memory-event payload; (b) `assert_no_outcome_leak` RAISES `OutcomeLeakError` if the sentinel reaches `instruction.md`/`task.toml`; (c) the adapter SUCCESSFULLY projects a known-good fork_point into a `cut` event and a known-good memory-event into a `MemoryEvent` (positive path); (d) a Session with a novel unknown field makes the projector RAISE. Test FAILS today — correct red.
2. **Write `memory-bench/membench/openrath_adapter.py`** as an allow-list projector turning the leak-safe subset green: `Session → (list[cut events, source=openrath-runtime, history_state='recorded'], list[MemoryEvent], QueryWork)`, SHA-guarded fork-point, RAISE on unrecognized fields, EXCLUDE `used`/`replay`.
3. **Run the reconstruction-tax diagnostic offline** on closed beads that also have a `capture-provenance.sh` merge-base record, sourcing the runtime fork-point from an INDEPENDENT signal (claim-time HEAD), and emit the agreement-rate (both values, tagged by source, never collapsed). If no independent source exists, mark UNMEASURABLE and HALT.

This single diff tells us how much reconstruction tax we actually pay, proves mem's already-shipped firewall is sufficient for *projected* Sessions before any eval-design escalation, and is solo-dev-mergeable with no result number — fully inside the publication-freeze.

## Folded-in related work (2026-06-21, Stephanie-supplied)

### Focused — directly changes this PRD

**grite — "Before the Pull Request: Mining Multi-Agent Coordination"** (Sarkar,
arXiv 2606.19616). A decentralized coordination substrate that uses **git
itself as an append-only, signed event log** of how concurrent agents
claim / divide / collide over shared work, *before* a PR exists. Reports
duplicate work 78%→0%, throughput >3×, and surfaces failure modes
"invisible in pull-request history."

- *What it changes:* a **second runtime-native provenance source**, and a
  git-native one (no separate store) — it captures the **pre-PR
  coordination signal** mem reconstructs post-hoc, complementary to
  OpenRath's Session lineage. grite is arguably the cleaner native source
  for the *used-retrieval-causality / who-collided-with-whom* keys that
  OpenRath's `lineage` alone doesn't carry.
- *Validity note:* coordination events are **pre-outcome**, so safer as
  input than outcome lineage — but they still pass through the same
  allow-list projector + leak scan (a `claim` event can embed a
  `commit_sha`). No firewall relaxation.
- *Benchmark angle:* grite's "duplicate-work 78%→0%" is itself a
  **coordination-effectiveness outcome** mem could replay/benchmark — a
  candidate task family for the work-audit corpus.

**PACMS — "Submodular Context Selection as a Pluggable Engine for LLM
Agents"** (Ghulyani et al., arXiv 2606.20047). Relevance-aware **submodular
selection at prompt-assembly time** over a pooled candidate set (memory
entries + conversation turns + tool outputs), replacing recency truncation
(which is "topic-blind").

- *What it changes:* this is the principled retrieval/selection layer for
  mem's retrieval-v1 — and, more immediately, a **directly-relevant new
  baseline ARM for the live `mem-lxs1` foraging adaptive-K study**:
  submodular-selection-at-matched-budget sits alongside fixed-K, per-query
  score-gap, LLM-loop, and the foraging stop-controller. It is the
  deployment-realistic "smart constant-budget" baseline the foraging arm
  must beat on cost/latency, not just fixed-K. *Recommend adding a PACMS arm
  to lxs1.*
- Free/local-compatible (submodular optimization is arithmetic over
  embeddings) — clears the scix no-paid-API constraint.

### General memory-infrastructure references (captured, not folded into the OpenRath plan)

- **Perplexity Brain — "Self-Improving Memory for Agents"** (Research
  Preview, 2026-06-18). The closest *productized* analog to mem's whole
  thesis: a **context graph of the agent's WORK** (what succeeded / failed /
  needed correction), **every entry provenance-linked to its session / file
  / source**, with an **overnight synthesis** that distills logs into
  reusable lessons auto-loaded into each run. First-party numbers: **+25%
  correctness, +16% recall, −13% cost** on history-dependent workflows.
  *Why it matters to us:* it externally validates mem's bet (outcome-tagged
  work memory lifts future work) AND sharpens mem's differentiator — those
  are **first-party self-reported** numbers on a closed system; mem's edge
  is the **verifiable-outcome benchmark + the validity firewall** (no
  outcome leakage, third-party replicable). Note the name collision with our
  own `~/brains`. Directly informs the `~/brains` consolidation design
  (overnight synthesis, per-lesson provenance).
- **Elastic — "Agent Memory on Elasticsearch."** A concrete enterprise
  reference architecture for `~/brains`: **index-per-lifecycle**
  (episodic / semantic / procedural — different write rates, decay,
  consolidation); hybrid BM25 + dense + RRF + cross-encoder rerank;
  **consolidation with `supporting_episode_ids` (provenance for derived
  facts)**; **supersession-over-deletion with an audit chain** (never
  delete, mark `superseded_by`); decay + use-count scoring; **DLS
  multi-tenant isolation**; and **CI eval gates with *zero cross-tenant
  leaks*** (R@10 ≥ 0.85). The zero-leak eval gate is the *same discipline*
  as mem's validity firewall, and **procedural memory with success/failure
  counters is outcome-linked memory** — mem's thesis in production form.
- **SIGMA — "Skill-Incidence Graphs for Compositional Multi-Agent Design"**
  (Zeng et al., arXiv 2606.19758). *Honest correction:* despite being filed
  under "memory infrastructure", this is a multi-agent **composition** paper
  (task-conditioned skill bundles, topology decoding) — it does **not**
  address memory architecture, retrieval, or consolidation. Relevant to Gas
  City **orchestration/composition**, not to the memory stack. Captured here
  so it isn't mistaken for a memory-infra input.

*Net effect on the plan:* no change to the Phase-0/Phase-1 firewall-first
spine. Two concrete additions surface — (a) **grite as a second, git-native
provenance source** to evaluate alongside OpenRath in Phase 1; (b) **a PACMS
selection arm for `mem-lxs1`**. Perplexity Brain + Elastic are reference
inputs for the separate `~/brains` consolidation design, not the OpenRath
adapter.
