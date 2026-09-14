# bd adoption harness

A harness for one question: **will a coding agent choose a memory tool on its own,
recover what it stored in a later session, and act on it?**

Task success cannot answer that question by itself. In our first run, the agents that
were given only general "you have persistent memory" guidance solved their tasks
without touching bd at all. They wrote the facts to Claude Code's own memory files
instead. The task passed; bd was never used. The harness therefore measures the
pathway as well as the outcome, and it reads that pathway from execution receipts
rather than from what the agent reports doing.

Agent trials run against the real `claude` CLI and a real `bd` binary. There is no
simulation mode. Only the `--fire` step starts paid sessions; installation, the local
smoke check, corpus generation, planning, and reporting do not call a model.

## What one trial looks like

A trial is a **pair** of agent sessions against the same synthetic project.

1. **Establish session.** The agent works a task and encounters several facts, one of
   which is an opaque token it cannot guess or re-derive. Nothing tells it to save
   anything specific.
2. The harness clears the working directory. The bd store and the native memory
   directory survive.
3. **Goal session.** A fresh agent, with no conversation history, gets a task that
   needs that token.

Every task ships as twins:

| Variant | Goal prompt | What a bd read means |
|---|---|---|
| `necessary` | omits the token | the agent had to recover it from memory |
| `unnecessary` | supplies the token | reading memory was avoidable work |

The `unnecessary` twin is the control. Without it, a condition could score well on
adoption purely by making the agent read memory constantly.

### The four-session trial (`--legs trial`)

The pair asks whether a fact survives a handoff. The trial asks what happens when the
fact *changes*: it spends four sessions on one store instead of two.

1. **Establish.** As above, but the token the agent encounters is the *previous*
   version of the fact.
2. **Revise.** A fresh agent is shown the previous version and the current one, in that
   order, and told the fact changed. Whether it overwrites what the first session
   stored, writes beside it, or writes nothing is recorded per bd call, from bd's own
   acknowledgement (`Updated [key]` is an in-place overwrite, `Remembered [key]` a new
   entry).
3. **Goal.** A fresh agent needs the current version. Beyond the pair's endpoints, the
   trial records whether what it read back states only the current version or still
   carries the superseded one.
4. **Stale writer.** A fresh agent primed on the *previous* version, as if resuming
   after the revision landed, is asked to record what it knows. Whether its write
   overwrites the current version, lands beside it, or is rejected, and what it does
   after a rejection (stop, re-read, retry), is the observation a CLI test cannot
   make on its own.

Every verdict is read from recorded bd output, never inferred from the command line.
A call whose output cannot be pinned on one direct `bd` command (a pipe, a second
command on the same line, a wrapper) is reported as unattributed rather than scored. A
redirection that leaves stdout alone (`2>&1`, `2>/dev/null`) does not make a command
compound: agents append one to nearly every bd call, and scorer versions before 5 read
every such call as unattributed. The audit below rescores saved sessions with the
current scorer, so a run scored under an older version need not be bought again.

## The three conditions

All three run the same tasks, the same tools, the same isolated store lifecycle, and
the same default native-memory settings. Only the guidance differs.

| Condition | What the agent is told | Native memory |
|---|---|---|
| `generic` | general persistent-memory guidance, bd never named | observed |
| `explicit` | bd deployment context plus working `remember` / `recall` / `memories` examples | observed |
| `redirect` | the explicit context, plus a hook that blocks a native-memory access and answers it by naming the bd commands | intercepted |

"Observed" means a `PreToolUse` hook records the reach and lets it through.
"Intercepted" means the same hook refuses the call and returns a message pointing at
bd. The recognizer that decides what counts as a native-memory access is shared with
the scoring path, so the hook and the scorer cannot drift apart.

## What we measured with it

96 pairs, 192 sessions, `claude-sonnet-4-6` on Claude Code 2.1.261, 8 generated task
clusters, $13.47 of estimated spend.

The table shows the necessary twins (16 pairs per condition); the other 48 pairs
are unnecessary-memory controls. Redirect recall also has one unknown outcome.

| Condition | Captured the token | Recalled it | Recalled it *before* acting | Full bd handoff | Qualifying Write |
|---|---:|---:|---:|---:|---:|
| `generic` | 0/16 | 0/16 | 0/16 | **0/16** | 16/16 |
| `explicit` | 10/16 | 9/16 | 8/16 | **8/16** | 14/16 |
| `redirect` | 15/16 | 15/16 | 12/16 | **12/16** | 12/16 |

Two details in that table matter as much as the headline. The `generic` row is an
agent that never discovers the tool and produces a qualifying Write every time anyway.
And qualifying Writes fall as adoption rises, because several agents that did recall
the token then misread what it was for. This action check is narrower than complete
task correctness: it does not verify every configuration field or the final filesystem.

Full write-up: [RESULTS.md](RESULTS.md). Definitions and known limits:
[METHODS.md](METHODS.md).

## Prerequisites

- Python 3.12, [uv](https://docs.astral.sh/uv/), Git, and a POSIX shell (Linux or macOS).
  Run everything from `memory-bench/`.
- The `claude` CLI on `PATH`. Pin the version you intend to run with; the harness
  refuses to start if the installed version differs from what you pass.
- A `bd` binary on `PATH`, or an absolute path in `MEMBENCH_BD_BINARY`. The harness
  records its path, SHA-256 and version into the run manifest and refuses to resume a
  run if the binary underneath it changed.
- `CLAUDE_CODE_OAUTH_TOKEN` set. Generate one with `claude setup-token`.
- `ANTHROPIC_API_KEY` **unset**. A set key silently reroutes every child session onto
  the metered API, which is both a surprise bill and a different measurement from the
  one the manifest claims. The harness refuses to spend while it is set.

```bash
cd memory-bench
uv sync
```

### Install the reference bd version

The known-working baseline is **bd v1.3.0-rc.1, commit `9c6a69ec1`**. Use an
embedded-capable release binary: a server-only build cannot create the harness's
isolated local stores. Other versions are experimental, not validated substitutes.
The [upstream release](https://github.com/gastownhall/beads/releases/tag/v1.3.0-rc.1)
provides pinned archives and checksums; package-manager defaults may select a
different version.

This installs a separate binary without replacing your team's existing `bd`:
the download recipe requires `curl`, `tar`, and `shasum`.

```bash
# Choose linux_amd64, linux_arm64, darwin_amd64, or darwin_arm64.
bd_platform=linux_amd64
bd_archive="beads_1.3.0-rc.1_${bd_platform}.tar.gz"
bd_release=https://github.com/gastownhall/beads/releases/download/v1.3.0-rc.1
mkdir -p "$HOME/.local/opt"
bd_install_dir="$(mktemp -d "$HOME/.local/opt/membench-bd-1.3.0-rc.1.XXXXXX")"
(
  set -eu
  cd "$bd_install_dir"
  curl -fL "$bd_release/$bd_archive" -o "$bd_archive"
  curl -fL "$bd_release/checksums.txt" -o checksums.txt
  awk -v asset="$bd_archive" '$2 == asset {print; found=1} END {if (!found) exit 1}' \
    checksums.txt | shasum -a 256 -c -
  tar -xzf "$bd_archive" bd
)
export MEMBENCH_BD_BINARY="$bd_install_dir/bd"
"$MEMBENCH_BD_BINARY" --version
# bd version 1.3.0-rc.1 (9c6a69ec1)
```

Keep this directory and export the same absolute path in later shells. Moving or
upgrading the binary changes the frozen run identity and prevents resuming it.

### Check the local store (no agent, no credentials)

Run this before freezing a plan. It exercises the same isolated store and command
shim used by the harness, then removes only its temporary store:

```bash
PYTHONPATH=. uv run python - <<'PY'
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from membench.runner.bd_experiment import resolve_bd_identity
from membench.runner.tool_surface import harness_call, provision_memory_tool

print(json.dumps(resolve_bd_identity(), indent=2))
with TemporaryDirectory(prefix="membench-bd-check-") as root:
    surface = provision_memory_tool(Path(root), sandbox=None)
    key, token = "membench-check", "membench-check-token-73f91"
    harness_call(surface, ["remember", token, "--key", key])
    assert harness_call(surface, ["recall", key]).strip() == token
    assert key in harness_call(surface, ["memories", "membench-check"])
print("PASS: isolated bd remember / recall / memories round-trip")
PY
```

A missing command, failed initialization, or failed assertion means stop before
`--fire`. This checks local wiring, not agent adoption or Claude authentication.

## Build the task corpus

The corpus is generated deterministically from seeds, so it is not committed. Eight
seeds reproduce the exact corpus our published numbers were measured on:

```bash
for s in 0 1 2 3 4 5 6 7; do
  PYTHONPATH=. uv run python scripts/generate_worlds.py \
    --seed "$s" --personas 4 --tasks 2 --offline --tool-requiring \
    --out fixtures/worlds-tool
done

PYTHONPATH=. uv run python scripts/verify_worlds.py fixtures/worlds-tool
# 8/8 worlds reproduce deterministically
```

`--offline` means the persona rows are authored by seeded code, with no model call and
no network. Each world is admitted only if a memory-necessity gate confirms the task
genuinely cannot be solved without the retained facts.

If your corpus is right, the plan you freeze in the next step will carry
`"corpus_fingerprint": "24c77b5bc5c10e5d"`, the same fingerprint as our run.

## Freeze a plan (spends nothing)

Without `--fire`, the entry point only writes a manifest. No agent starts.

```bash
PYTHONPATH=. uv run python -m membench.runner.bd_experiment \
  --out runs/my-first-run \
  --model claude-sonnet-4-6 \
  --expect-cli-version "$(claude --version | cut -d' ' -f1)" \
  --tasks 8 --repeats 2
```

The manifest holds the full randomized schedule, the model, the CLI version, the bd
identity, a hash of every harness source file, the corpus fingerprint, and the leg
plan. Read it before spending anything. `--tasks 8 --repeats 2` is the shape of our
published run: 96 pairs, 192 sessions.

Add `--legs trial` to freeze the four-session version-history trial instead. The plan
is part of the frozen identity, so a directory planned as pairs cannot later be run as
trials, or the reverse; each schedule entry then spends four sessions on one store, and
`--max-pairs` still counts schedule entries.

## Run it

```bash
PYTHONPATH=. uv run python -m membench.runner.bd_experiment \
  --out runs/my-first-run \
  --model claude-sonnet-4-6 \
  --expect-cli-version "$(claude --version | cut -d' ' -f1)" \
  --tasks 8 --repeats 2 \
  --fire --max-pairs 1
```

`--max-pairs` bounds how many *new* pairs this invocation may buy. It defaults to 1, so
the obvious first command buys one pair and stops. Re-run with a larger `--max-pairs` to
continue; already-completed pairs are reused, never repurchased. Budget roughly **$0.14
per pair** at the rates our run saw, so a full 96-pair replication is around $13. A
trial runs twice the sessions of a pair, so budget roughly twice that per entry; pass
the same `--legs trial` on every invocation against a trial directory.

Start with one pair and read its output before scaling up.

### If a run is interrupted

A pair whose directory exists without a `cell.json` is treated as a **purchased but
unreconciled** trial. The next invocation refuses to start rather than silently buying
a replacement, because that process may have spent money before it wrote anything.
Inspect `runs/<name>/pairs/<condition>/<hash>/<repeat>/` (it will hold `started.json`
and any completed `legs/`, plus a `halt.json` recording what went wrong), decide what
happened, then move or delete that directory deliberately.

## Read the results

```bash
PYTHONPATH=. uv run python scripts/analyze_bd_experiment.py runs/my-first-run \
  --out runs/my-first-run-analysis
```

That writes `analysis.json` and a human-readable `report.md`; the pair from our own run
is in [`reference-run/`](reference-run/report.md) if you want to see the shape before you
spend anything. Denominators come from the *schedule*, not from whatever completed, so a
missing or unmeasured pair stays visible in the counts instead of quietly shrinking the
sample. The analyzer reads the leg plan from the manifest: a trial run gets a second
table tallying the revision, stale-write and after-rejection verdicts per condition,
with unknowns (missing legs, unattributable calls) counted beside the observed
categories rather than dropped.

A transcript audit rechecks acknowledged `Write` calls against the expected
`config.json` path and JSON string values, and checks recall/action ordering:

```bash
PYTHONPATH=. uv run python scripts/audit_bd_actions.py runs/my-first-run \
  --out runs/my-first-run-audit
```

On our 96 reference pairs, this audit reported no goal-action disagreements with the
saved scores. It reads recorded tool arguments and acknowledgments, **not the actual
file or final filesystem state**. It checks required and superseded tokens, not their
semantic field placement or every requested setting. Current runs and the audit share
the same `bd_actions.write_reason` validator, so agreement is not independent evidence
of correctness. The audit remains useful for inspecting individual writes, timing,
missing evidence, and discrepancies with historical saved scores.

The same audit rescores every saved session with today's scorer, from its transcript
and bd receipts, and for a trial run derives the trial verdicts again. `audit.json`
carries the saved and the rescored verdicts per pair (`trial_saved`, `trial_rescored`)
and the rescored evidence per session (`rescored_legs`); `report.md` shows the two side
by side, and lists the pairs whose verdicts moved. A moved verdict is a scorer change,
not new agent behaviour. Rescoring passes no config directory, so the native-memory
counters are not compared.

### Replay a session as a recording

Every saved session can be replayed as an asciinema recording, and as a GIF and MP4
when `agg` and `ffmpeg` are installed. Nothing is re-run: every command, result and
receipt shown is the recorded one, paced for reading.

```bash
PYTHONPATH=. uv run python scripts/render_leg_cast.py runs/my-first-run \
  --out runs/my-first-run-recordings --video \
  --audit runs/my-first-run-audit/audit.json
```

The closing block of each recording shows the score saved at run time. With `--audit`,
the rescored evidence is shown above it, each block labelled with the scorer version
that produced it.

### Layout of a run directory

```
runs/my-first-run/
  manifest.json                      frozen identity and schedule
  pairs/<condition>/<task-hash>/<repeat>/
    started.json                     pair identity, written before any spend
    legs/                            one row per session: argv, exit status, receipts
                                     (two rows for a pair, four for a trial)
    cell.json                        scored outcome, written last
    halt.json                        present only if the pair failed
```

Each leg row names its role (`establish`, `goal`, and for a trial `revise` and
`stale_writer`) and its position in the plan; the analyzer and the audit refuse a row
filed under a role at the wrong position.

Every bd invocation inside a session is wrapped so that its actual argv, exit status
and per-stream output are recorded. A verb on a command line is not an operation: in
an earlier run the only apparent "memory write" was a `bd remember list` that bd
*refused*, and only the recorded result could say so.

## Refusals you may hit

The harness stops before spending, with the reason, when:

- `ANTHROPIC_API_KEY` is set
- `CLAUDE_CODE_OAUTH_TOKEN` is unset
- the model is not pinned
- the installed `claude` version differs from `--expect-cli-version`
- the resolved `bd` binary differs from the one in the frozen manifest
- the output directory holds a plan that disagrees with the one about to run
- a previously started pair has no recorded outcome

These are ordered so that no refusal message sends you into a different refusal.

## What this does not tell you

Eight generated task clusters, one model, repeats inside a single episode. The result
is a bounded adoption comparison, not a production reliability estimate. The tasks use
opaque synthetic tokens, which agents sometimes reject as suspicious or mistake for
lookup keys; those failures are part of the numbers rather than repaired after the
fact. The harness measures bd used as a CLI through Bash, not a memory tool exposed
over MCP. And a full handoff is an observed conjunction, not proof the action depended
on bd when another channel could also have supplied the value.

[METHODS.md](METHODS.md) states these limits in full, along with the phase differences
between the pilot and the confirmation run.
