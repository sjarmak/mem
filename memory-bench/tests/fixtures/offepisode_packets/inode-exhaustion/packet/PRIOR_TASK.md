Source event line 7; timestamp 2026-08-27T06:40:00Z

○ Harbor-t3v9 [BUG] · Sweep writes fail with "No space left on device" on a volume df reports as two thirds free   [● P1 · OPEN]
Owner: harbor-worker-gc-518330 · Type: bug
Created: 2026-08-26 · Updated: 2026-08-27

DESCRIPTION

  The message is the ordinary out-of-space one and the volume is not out of
  space. That is the whole report.

  The write site is run/artifacts.go:66, the only place the sweep opens a
  file for writing:

      func writeArtifact(dir, name string, body []byte) error {
          return os.WriteFile(filepath.Join(dir, name), body, 0o644)
      }

  Since Tuesday it returns:

      open /var/lib/harbor/artifacts/run-88412/stdout.txt: no space left on
      device

  df on that mount reports 66 percent of the blocks free. df -i on the same
  mount, same second, reports the inodes 100 percent used. Both dumps are in
  the source packet.

  Where the inodes went: the sweep writes one directory per run and four
  small files inside it, and nothing deletes them. find counts 2,481,904
  entries under /var/lib/harbor/artifacts, and the per-run directories go back
  to the first sweep on this box.

  What the caller does with the error: run/sweep.go:203 wraps it as "artifact
  write failed" and continues to the next task, so the run finishes and is
  reported complete with an empty artifact directory.

  IMPACT: a run that finishes with no artifacts still counts as a run. The
  cost rollup reads the artifact directory and reports zero where it should
  report a number, and there is no other copy of the output.

  FIX: not decided here. Deleting old runs, moving the artifacts to another
  mount, and recreating the filesystem are three different changes, and the
  evidence supplied does not say which of them this box needs.

NOTES

  gate: the mount is shared with the paid sweep window, and the deletion is
  irreversible. Routed to a human on the deletion, not on the diagnosis.

LABELS: needs-human

METADATA
  gc.work_dir: /workspace/projects/harbor
  work_dir: /workspace/projects/harbor
