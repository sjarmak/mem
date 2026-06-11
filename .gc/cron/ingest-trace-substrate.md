# Cron: nightly trace-substrate ingest (mem-75t.4)

Keeps the mem `.mem/store.db` sidecar's trace substrate fresh: resolve
transcripts → parse errors + run-metadata → attach git provenance, then report
the coverage delta. The unattended form of the verified invocation documented in
the `/ingest-trace-substrate` skill.

The exec is `.gc/scripts/ingest-trace-substrate.sh` — mechanical, no LLM. It
builds into a scratch store, refuses to swap if zero traces resolved (the
wrong-cwd failure mode), and appends a coverage-delta line to
`.mem/ingest-trace-substrate.log`.

## Schedule

Nightly at 03:17 local (off the :00 mark so the fleet doesn't synchronize).

## Register it (pick one)

**System crontab — the only form that actually fires unattended.** A live agent
session is not required:

```cron
17 3 * * * /home/ds/projects/mem/.gc/scripts/ingest-trace-substrate.sh >> /home/ds/projects/mem/.mem/ingest-trace-substrate.log 2>&1
```

```bash
( crontab -l 2>/dev/null; \
  echo '17 3 * * * /home/ds/projects/mem/.gc/scripts/ingest-trace-substrate.sh >> /home/ds/projects/mem/.mem/ingest-trace-substrate.log 2>&1' \
) | crontab -
```

**Interactive Claude Code session (`CronCreate`).** Convenient while a REPL is
open, but the job only fires when the REPL is idle and is dropped when the
session exits (even `durable: true` still needs a live REPL), so it does not
replace the system-crontab entry for true unattended cadence:

```
CronCreate(
  cron: "17 3 * * *",
  prompt: "Run .gc/scripts/ingest-trace-substrate.sh and report the coverage delta line it appended to .mem/ingest-trace-substrate.log",
  recurring: true,
  durable: true,
)
```

## Coverage deltas

Each run appends one line:

```
2026-06-11T03:17:00Z OK records=6691 with_trace=1207 trace_errors=842 with_base_commit=331 delta={"records":0,"with_trace":4,...}
```

`delta: none` (all-zero) on a re-run is expected — the substrate is already
complete. An `ABORT` line means no transcripts resolved and the store was left
untouched; check that the exec ran from the gas-city checkout.
