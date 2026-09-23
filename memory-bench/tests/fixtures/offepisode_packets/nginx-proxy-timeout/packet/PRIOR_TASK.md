Source event line 11; timestamp 2026-08-26T11:15:00Z

○ Harbor-r5nc [BUG] · the edge proxy returns 504 on /api/reports/export for the two largest tenants, and only for them   [● P1 · OPEN]
Owner: harbor-worker-gc-505290 · Type: bug
Created: 2026-08-25 · Updated: 2026-08-26

DESCRIPTION

  Two tenants cannot export a report. Every other tenant can. The failure is
  the same every time and does not depend on retry.

  The edge proxy config for that path, in full:

  deploy/edge/reports.conf
      location /api/reports/export {
          proxy_pass       http://reports_api;
          proxy_read_timeout 30s;
      }

  The error log records, for each failed request:

      upstream timed out (110: Connection timed out) while reading response
      header from upstream, request: "GET /api/reports/export"

  The upstream is not down. Its own access log shows the same requests being
  served to completion after the proxy has already given up: the export
  handler reports upstream_response_time 41 for tenant acme-holdings and 44
  for tenant brightline, both of which 504 at the edge. Every tenant whose
  handler finishes in 4 to 9 seconds gets its report.

  So the upstream does the work and the answer is thrown away. The tenants
  that fail are the two with the largest row counts, which is why this reads
  as a size threshold rather than a per-tenant defect.

  IMPACT: an export that 504s is not retried by the client, and the user sees
  a generic failure page with no indication that the report was in fact
  produced. Support has been re-running these by hand.

  FIX: not decided here. The number in this config was chosen when the export
  handler was smaller and nobody re-derived it; whatever replaces it should be
  set against a measured handler time for the largest tenant, and the measured
  time belongs in the config comment so the next person does not have to
  rediscover it.

NOTES

  gate: touching the edge config affects every path behind the proxy, not just
  this location. Routed to a human on blast radius.

LABELS: needs-human

METADATA
  gc.work_dir: /workspace/projects/harbor
  work_dir: /workspace/projects/harbor
