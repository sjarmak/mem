Source event line 22; timestamp 2026-08-26T21:05:00Z

=== the error, copied from the sweep log ===

2026-08-26T20:58:41Z WARN run/sweep.go:203 artifact write failed
run=run-88412 err=open /var/lib/harbor/artifacts/run-88412/stdout.txt: no
space left on device

The same line repeats 11208 times between 2026-08-25T04:31:00Z and this
capture. Every occurrence carries a different run id and the same err text.

=== df, taken 2026-08-26T20:59:00Z, blocks ===

Filesystem      1K-blocks      Used Available Use% Mounted on
/dev/root        20961280   9110428  10802340  46% /
/dev/sdb1        41932800  13421772  26375028  34% /var/lib/harbor

=== df -i, same shell, same second, inodes ===

Filesystem        Inodes    IUsed  IFree IUse% Mounted on
/dev/root        1310720   201884 1108836   16% /
/dev/sdb1        2621440  2621440      0  100% /var/lib/harbor

=== the census the operator ran on the artifact tree ===

find /var/lib/harbor/artifacts -mindepth 1 | wc -l
2481904

find /var/lib/harbor/artifacts -mindepth 1 -maxdepth 1 -type d | wc -l
496380

The oldest per-run directory is dated 2026-02-11 and the newest is dated
2026-08-26. No entry under that tree was removed in that window; the
operator checked by comparing the directory count against the recorded
sweep count, which agree.

=== run/artifacts.go, lines 58 to 72 ===

  58    // writeArtifact stores one artifact for one run.
  59    //
  60    // Every run writes stdout, stderr, a manifest, and a timing file into
  61    // its own directory. Nothing in this package removes any of them.
  62    func artifactDir(runID string) string {
  63        return filepath.Join(rootDir, "artifacts", runID)
  64    }
  65
  66    func writeArtifact(dir, name string, body []byte) error {
  67        return os.WriteFile(filepath.Join(dir, name), body, 0o644)
  68    }
  69
  70    // caller, run/sweep.go:203
  71    //   if err := writeArtifact(d, "stdout.txt", out); err != nil {
  72    //       log.Warn("artifact write failed", "run", id, "err", err)

=== run/sweep.go, lines 200 to 208 ===

 200    for _, task := range batch {
 201        out, rc := execute(ctx, task)
 202        d := artifactDir(task.RunID)
 203        if err := writeArtifact(d, "stdout.txt", out); err != nil {
 204            log.Warn("artifact write failed", "run", task.RunID, "err", err)
 205        }
 206        results = append(results, Result{RunID: task.RunID, Code: rc})
 207    }
 208    return results, nil

=== the operator's alert rules, copied from the saved config ===

alert: disk_blocks_used_over_85_percent  source: df, per mount
alert: host_load_over_8                  source: node exporter
alert: sweep_task_failures_over_20       source: sweep exit codes
alert: api_error_rate                    source: gateway log

No rule on that page reads df -i, inode counts, or the sweep log's WARN
lines. The sweep exit code for every affected run was 0.
