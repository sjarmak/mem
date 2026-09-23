Source event line 31; timestamp 2026-08-24T09:19:00Z

=== the error, copied from the application log ===

2026-08-24T08:51:07Z DEBUG cache/session.go:147 put failed id=sess:9f31c4 err=
OOM command not allowed when used memory > 'maxmemory'.

The same line repeats 3,884 times between 2026-08-20T02:14:00Z and the dump
below. No line at any level above DEBUG mentions the session cache in that
window.

=== INFO memory, taken 2026-08-24T09:14:00Z, fields quoted as returned ===

used_memory:4294901760
used_memory_peak:4294967296
maxmemory:4294967296
maxmemory_policy:noeviction
mem_allocator:jemalloc

=== INFO stats, same connection, same second ===

expired_keys:118204
evicted_keys:0
keyspace_hits:9910442
keyspace_misses:2210518
total_connections_received:41208

=== INFO server, same connection, trimmed to the fields the operator captured ===

uptime_in_seconds:401164
config_file:/etc/redis/harbor-staging.conf
executable:/usr/bin/redis-server

=== evicted_keys and keyspace_misses, sampled hourly from the operator's own
=== scrape, four days, one row per day at 09:00Z

2026-08-21  evicted_keys 0  keyspace_misses 1104318
2026-08-22  evicted_keys 0  keyspace_misses 1512907
2026-08-23  evicted_keys 0  keyspace_misses 1866440
2026-08-24  evicted_keys 0  keyspace_misses 2210518

=== cache/session.go, lines 138 to 152 ===

 138    // Put stores one session blob under its id.
 139    //
 140    // The store is the only session backend; there is no fallback.
 141    func (s *Store) Put(ctx context.Context, id string, blob []byte) error {
 142        return s.rdb.Set(ctx, "sess:"+id, blob, s.ttl).Err()
 143    }
 144
 145    func (s *Store) putBestEffort(ctx context.Context, id string, blob []byte) {
 146        if err := s.Put(ctx, id, blob); err != nil {
 147            log.Debug("put failed", "id", "sess:"+id, "err", err)
 148        }
 149    }
 150
 151    // Get returns the blob, or ErrMiss when the key is not present.
 152    func (s *Store) Get(ctx context.Context, id string) ([]byte, error) {

=== the operator's dashboard panel list, copied from the saved board ===

panel: request rate
panel: p95 latency by route
panel: login failures per minute
panel: cache client errors (source: application log, level >= WARN)
panel: host memory used

Nothing on the board reads used_memory, maxmemory, evicted_keys, or
maxmemory_policy from the server itself.
