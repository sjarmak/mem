Source event line 27; timestamp 2026-08-26T11:40:00Z

=== edge proxy error log, /var/log/edge/error.log, filtered to the export path ===

2026/08/25 22:04:11 [error] upstream timed out (110: Connection timed out)
    while reading response header from upstream, client: tenant acme-holdings,
    server: edge, request: "GET /api/reports/export HTTP/2", upstream:
    "http://reports_api/api/reports/export", host: "app-harbor-example"

2026/08/25 22:19:03 [error] upstream timed out (110: Connection timed out)
    while reading response header from upstream, client: tenant brightline,
    server: edge, request: "GET /api/reports/export HTTP/2", upstream:
    "http://reports_api/api/reports/export", host: "app-harbor-example"

=== edge proxy access log, same two requests ===

tenant acme-holdings  GET /api/reports/export  504  request_time 30
tenant brightline     GET /api/reports/export  504  request_time 30

=== upstream access log, reports-api, same two requests ===

tenant acme-holdings  GET /api/reports/export  200  upstream_response_time 41
tenant brightline     GET /api/reports/export  200  upstream_response_time 44

=== upstream access log, the next eight tenants by row count ===

tenant cedarworks     GET /api/reports/export  200  upstream_response_time 9
tenant dunmore        GET /api/reports/export  200  upstream_response_time 8
tenant eastvale       GET /api/reports/export  200  upstream_response_time 8
tenant fairhaven      GET /api/reports/export  200  upstream_response_time 7
tenant glenrock       GET /api/reports/export  200  upstream_response_time 7
tenant hollybrook     GET /api/reports/export  200  upstream_response_time 5
tenant ironside       GET /api/reports/export  200  upstream_response_time 4
tenant junewood       GET /api/reports/export  200  upstream_response_time 4

=== deploy/edge/reports.conf, in full ===

upstream reports_api {
    server reports-api:8080;
}

location /api/reports/export {
    proxy_pass       http://reports_api;
    proxy_read_timeout 30s;
}

=== git log, deploy/edge/reports.conf ===

2025-11-04  edge: split the export path into its own location block
2025-11-04  edge: set a read timeout on the export path

=== row counts, reports source table, by tenant ===

acme-holdings   8241900
brightline      8003117
cedarworks      1442083
dunmore         1281740
