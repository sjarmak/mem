Source event line 14; timestamp 2026-08-24T09:05:00Z

○ Harbor-q7m2 [BUG] · Session cache writes start failing with OOM once the box has been up a few days, and the failure surfaces as a logged-out user   [● P1 · OPEN]
Owner: harbor-worker-gc-511904 · Type: bug
Created: 2026-08-23 · Updated: 2026-08-24

DESCRIPTION

  Reported as "people get logged out at random". It is not random and it is
  not the login path.

  cache/session.go:141 is the only write to the session cache:

      func (s *Store) Put(ctx context.Context, id string, blob []byte) error {
          return s.rdb.Set(ctx, "sess:"+id, blob, s.ttl).Err()
      }

  On the staging box, that Set has been returning an error since Thursday:

      OOM command not allowed when used memory > 'maxmemory'.

  The full error text and the server INFO dump are in the source packet.

  What the INFO dump shows: used_memory is above the configured maxmemory,
  maxmemory_policy is noeviction, and evicted_keys is zero for the whole
  uptime. keyspace_misses climbs steadily over the same window while
  keyspace_hits is flat.

  What the caller does with the error: cache/session.go:147 logs it at debug
  and returns nil. So a failed Put is indistinguishable from a successful one
  to everything upstream, the next read misses, and the user is sent back to
  the login page.

  IMPACT: the swallowed error is the part that matters. The store is the only
  session backend, the failure is silent by construction, and the box has been
  in this state for four days without a single alert firing. Nothing in the
  supplied dashboards graphs evicted_keys or used_memory against maxmemory.

  FIX: not decided here. Raising maxmemory, changing the policy, and fixing
  the swallowed return are three different changes with three different blast
  radii, and the evidence supplied does not say which of them the box needs.

NOTES

  gate: the box is shared with the paid sweep window and a restart clears the
  state being diagnosed. Routed to a human on the restart timing, not on the
  fix.

LABELS: needs-human

METADATA
  gc.work_dir: /workspace/projects/harbor
  work_dir: /workspace/projects/harbor
