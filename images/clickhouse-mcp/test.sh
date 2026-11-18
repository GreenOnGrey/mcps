#!/usr/bin/env bash
# A test of the image clickhouse-mcp (FTR.INFRA.MCPS-0001 R2, R3, R9, R11, R14): the checks are in
# test_server.py; this script runs them inside the container with the environment of the chart.
set -euo pipefail
image=${1:?the image}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
docker run --rm --read-only --tmpfs /tmp -u 10001 \
  -v "$here/test_server.py:/test_server.py:ro" \
  -e CLICKHOUSE_MCP_SERVER_TRANSPORT=http -e CLICKHOUSE_MCP_BIND_HOST=127.0.0.1 \
  -e CLICKHOUSE_MCP_AUTH_DISABLED=true -e CLICKHOUSE_MCP_QUERY_TIMEOUT=30 \
  -e CLICKHOUSE_HOST=127.0.0.1 -e CLICKHOUSE_PORT=9100 -e CLICKHOUSE_SECURE=false -e CLICKHOUSE_VERIFY=false \
  -e CLICKHOUSE_USER=mcp-placeholder -e CLICKHOUSE_PASSWORD=placeholder \
  -e CLICKHOUSE_LIMIT_SECONDS=30 -e CLICKHOUSE_LIMIT_ROWS=10000 -e PORT=8000 -e HOST=127.0.0.1 \
  --entrypoint python "$image" /test_server.py
