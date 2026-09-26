# `bd-passive-call.v1`

`bd-passive-call.v1` is the exchange contract between the fleet-side `bd`
logger and mem's passive-call rollup. The logger writes one JSON object per
invocation as JSONL. Mem validates every object strictly and rejects an entire
input file when a line is invalid.

```json
{
  "schema": "bd-passive-call.v1",
  "ts": "2026-09-23T12:00:00.000Z",
  "session": "gc-1011223",
  "labels": {
    "agent": "mem-worker-pool-3",
    "template": "mem/mem-worker",
    "rig": null,
    "runtime": "codex",
    "runtime_version": null,
    "model": null
  },
  "origin": "agent",
  "verb": "show",
  "flags": ["--json"],
  "positional_count": 1,
  "argv_chars": 21,
  "key_hash": null,
  "positional_hashes": ["9c92fba8e03db01f"],
  "exit": 0,
  "duration_ms": 84
}
```

## Producer rules

- `ts` is a real RFC 3339 UTC timestamp ending in `Z`.
- `session` is `GC_SESSION_ID`. All six labels are required. `agent`, `template`,
  and `runtime` are non-empty strings; `rig`, `runtime_version`, and `model` are
  either non-empty strings or `null` when the producer cannot identify them.
- `origin` is `BD_CALL_ORIGIN` and is either `agent` or `hook`.
- `verb` is the first non-flag token, `<unknown>` when none is identifiable, or
  `<flag>` when the token has flag shape. `flags` contains unique, lexically
  sorted flag names only; no `--flag=value` or flag values are allowed.
- `positional_count` counts positional arguments after the verb.
  `positional_hashes` has exactly one HMAC for each of those arguments, in the
  same order.
- `key_hash` is the HMAC for the `--key` value, or `null` when `--key` is not
  present. Hashes are `HMAC-SHA256(operator_salt, raw_value)`, lowercase hex,
  truncated to 16 characters.
- `argv_chars` is the character count of the original argument vector. `exit`
  and `duration_ms` are non-negative integers.
- The line must never contain stdout, stderr, the raw argv, raw flag values, or
  raw positional values. Unknown fields are rejected.

The machine-readable field schema is
[`schemas/bd-passive-call.v1.schema.json`](schemas/bd-passive-call.v1.schema.json).
The runtime validator additionally enforces the two cross-field constraints
listed in that schema's `x-mem-cross-field-constraints` extension.

## Rollup

Run `mem passive-rollup FILE [FILE ...]`; add `--json` for the standard mem
envelope. The `bd-passive-rollup.v1` result reports calls and calls/session,
verb counts, calls whose exit is non-zero, and successful read invocations that
share a hashed key with an earlier successful write in the same session. It
also computes the same metrics independently for every value of every label.
Nullable labels use the visible `"<null>"` key in the per-label breakdown.
String values equal to `"<null>"` or beginning with `\` gain a leading `\` so
they remain distinct from null and from each other.

The read/write join is deliberately mechanical and correlational. It uses an
allow-list of unambiguous top-level verbs; verbs whose subcommand can change
read/write behavior do not participate. It does not claim that a prior write
caused the later read.
