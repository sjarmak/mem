Source event line 31; timestamp 2026-08-11T10:05:00Z

=== deploy log, staging, release train 14 ===

09:41:58  migrate  applying 0042_add_orders_created_at_idx.sql
09:42:45  migrate  applied  0042_add_orders_created_at_idx.sql
09:42:45  migrate  1 migration applied, exit 0

=== psql session, captured by the on-call while 0042 was still running ===

staging=> SELECT pid, mode, granted
staging->   FROM pg_locks
staging->  WHERE relation = 'orders'::regclass
staging->  ORDER BY granted DESC, pid;

  pid  |       mode       | granted
-------+------------------+---------
 20114 | ShareLock        | t
 20117 | RowExclusiveLock | f
 20118 | RowExclusiveLock | f
 20121 | RowExclusiveLock | f
 20122 | RowExclusiveLock | f
 20124 | RowExclusiveLock | f
 20125 | RowExclusiveLock | f
 20129 | RowExclusiveLock | f
 20130 | RowExclusiveLock | f
 20133 | RowExclusiveLock | f
 20134 | RowExclusiveLock | f
 20136 | RowExclusiveLock | f
(12 rows)

staging=> SELECT count(*) FROM pg_stat_activity
staging->  WHERE wait_event_type = 'Lock'
staging->    AND query LIKE 'INSERT INTO orders%';

 count
-------
    11
(1 row)

staging=> SELECT reltuples::bigint FROM pg_class WHERE relname = 'orders';

 reltuples
-----------
  18042199
(1 row)

staging=> SELECT query, state, now() - query_start AS waited
staging->   FROM pg_stat_activity WHERE wait_event_type = 'Lock' LIMIT 1;

           query            |        state        |  waited
----------------------------+---------------------+----------
 INSERT INTO orders (...)   | active              | 00:00:39
(1 row)

=== application log, checkout service, same window ===

09:41:58  checkout  order write queued
09:42:45  checkout  order write committed, queued for 47 seconds
09:42:45  checkout  drained 11 queued writes

=== dashboard poller, same window ===

09:41:50  poll orders ok
09:42:00  poll orders ok
09:42:10  poll orders ok
09:42:20  poll orders ok
09:42:30  poll orders ok
09:42:40  poll orders ok
09:42:50  poll orders ok
