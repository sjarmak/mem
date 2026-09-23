Source event line 22; timestamp 2026-08-19T14:31:00Z

=== go test -race ./pool/ ===

==================
WARNING: DATA RACE
Write at 0x00c0000b4180 by goroutine 34:
  harbor/pool.(*Pool).record()
      /workspace/projects/harbor/pool/collect.go:88 +0x84
  harbor/pool.(*Pool).runTask()
      /workspace/projects/harbor/pool/collect.go:141 +0x1f8
  harbor/pool.NewPool.func1()
      /workspace/projects/harbor/pool/collect.go:52 +0x64

Previous write at 0x00c0000b4180 by goroutine 7:
  harbor/pool.(*Pool).record()
      /workspace/projects/harbor/pool/collect.go:88 +0x84
  harbor/pool.(*Pool).runTask()
      /workspace/projects/harbor/pool/collect.go:141 +0x1f8
  harbor/pool.NewPool.func1()
      /workspace/projects/harbor/pool/collect.go:52 +0x64

Goroutine 34 (running) created at:
  harbor/pool.NewPool()
      /workspace/projects/harbor/pool/collect.go:52 +0x1b4
  harbor/pool_test.TestPoolConcurrentRecord()
      /workspace/projects/harbor/pool/collect_test.go:31 +0x9c
==================
--- FAIL: TestPoolConcurrentRecord (2s)
    testing.go:1399: race detected during execution of test
FAIL
FAIL    harbor/pool     3s
FAIL

=== go test ./pool/ -run TestPoolConcurrentRecord -count=5 ===

ok      harbor/pool     1s
ok      harbor/pool     1s
ok      harbor/pool     1s
ok      harbor/pool     1s
ok      harbor/pool     1s

=== the same five runs, with the harness dumping len(p.results) after each ===

run 1  tasks reported 50  results map 50
run 2  tasks reported 50  results map 46
run 3  tasks reported 50  results map 50
run 4  tasks reported 50  results map 48
run 5  tasks reported 50  results map 50

=== pool/collect.go, lines 84 to 92 ===

  84    // record files one finished task. Called from every worker.
  85    //
  86    // The map is created once in NewPool and lives for the run.
  87    func (p *Pool) record(id string, r Result) {
  88        p.results[id] = r
  89    }
  90
  91    // snapshot copies the results out once the pool has drained.
  92    func (p *Pool) snapshot() map[string]Result {
