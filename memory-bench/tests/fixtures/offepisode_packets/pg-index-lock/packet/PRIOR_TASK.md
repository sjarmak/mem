Source event line 14; timestamp 2026-08-11T09:42:00Z

○ Harbor-q7m2 [BUG] · migration 0042 holds a ShareLock on orders and queues every write for the length of the index build   [● P1 · OPEN]
Owner: harbor-worker-gc-501882 · Type: bug
Created: 2026-08-10 · Updated: 2026-08-11

DESCRIPTION

  Found during the staging deploy of release train 14 (2026-08-10). Writes to
  orders stopped for the duration of the migration and resumed on their own
  when it finished. Nothing errored, so the deploy check recorded the release
  green.

  The migration is one statement:

  db/migrations/0042_add_orders_created_at_idx.sql
      CREATE INDEX idx_orders_created_at ON orders (created_at);

  While it ran, pg_locks showed the building backend holding a ShareLock on
  orders, and eleven backends running INSERT INTO orders waiting on that lock.
  The waiters cleared in one batch when the build committed. Write stall
  measured from the first waiting backend to the last one granted: 47 seconds.

  orders carries 18042199 rows in staging. Production is larger and has not
  been deployed to.

  Reads were unaffected throughout. The reporting dashboard polled orders on a
  ten second interval across the whole window and every poll returned.

  IMPACT: a write stall of this length inside a deploy window is invisible to
  the deploy check, which asserts only that the migration process exits zero.
  The checkout path writes to orders on every order placement, so this is a
  customer-visible pause rather than an internal one.

  FIX: not decided here. Whatever replaces this statement has to be verified
  against pg_locks on a table of comparable size before it is trusted, because
  the lock a statement takes is the thing that matters and it is not visible
  in the migration text.

NOTES

  gate: the migration is already merged and has run in staging only. Routed to
  a human because reverting it where it has already applied is a different
  operation from not shipping it.

LABELS: needs-human, deploy

METADATA
  gc.work_dir: /workspace/projects/harbor
  work_dir: /workspace/projects/harbor

BLOCKS
  ← ○ Harbor-t8sd: release train 14 staging soak ● P2
