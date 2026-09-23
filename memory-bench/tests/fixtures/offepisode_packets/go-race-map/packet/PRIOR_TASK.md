Source event line 9; timestamp 2026-08-19T14:20:00Z

○ Harbor-w4k8 [BUG] · Pool.record writes p.results from every worker goroutine, and the run silently loses entries when the detector is off   [● P1 · OPEN]
Owner: harbor-worker-gc-503771 · Type: bug
Created: 2026-08-18 · Updated: 2026-08-19

DESCRIPTION

  Found while chasing a short results file, not while chasing a crash. The
  crash never happened; the counts were wrong.

  pool/collect.go:88 is the only write to p.results:

      func (p *Pool) record(id string, r Result) {
          p.results[id] = r
      }

  record is called from each worker goroutine as its task finishes. p.results
  is a plain map[string]Result created once in NewPool and never guarded.

  Under go test -race, TestPoolConcurrentRecord reports a DATA RACE on that
  line: write by goroutine 34, previous write by goroutine 7, both inside
  record. Full detector output is in the source packet.

  Without -race the same test passed all five runs. In two of them it passed
  its assertions and still produced a short map: 46 entries in one run and 48
  in another, where 50 tasks had reported. No error was raised in either.

  IMPACT: the short map is the part that matters. A racing write here does not
  announce itself in the harness output, and the results file it produces is
  the input to the cost rollup, so a run can lose two or four tasks and still
  be reported complete. The detector is not enabled in the harness suite, only in
  the package suite, so this shape can reach a result set unobserved.

  FIX: not decided here. Whatever guards this map has to be checked against a
  run under -race AND a repeat run without it, because the two runs fail in
  different ways and only one of them says anything out loud.

NOTES

  gate: the fix is small but pool is shared by both harness entry points and
  the change lands inside a paid sweep window. Routed to a human on the
  scheduling entanglement, not on the fix.

LABELS: needs-human

METADATA
  gc.work_dir: /workspace/projects/harbor
  work_dir: /workspace/projects/harbor
