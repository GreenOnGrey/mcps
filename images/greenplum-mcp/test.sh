#!/usr/bin/env bash
# A test of the image greenplum-mcp (FTR.INFRA.MCPS-0002 R2, R3, R5, R8, R9, R14): the checks are in
# test_server.py; this script runs them inside the container with the environment of the chart.
# GP_HOST is deliberately empty: the refusal of a write must come before any connection.
set -euo pipefail
image=${1:?the image}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
docker run --rm --read-only --tmpfs /tmp -u 10001 \
  -v "$here/test_server.py:/test_server.py:ro" \
  -e GP_HOST= -e GP_PORT=5432 -e GP_DB=citydrive_db -e GP_SSLMODE=prefer \
  -e GP_LIMIT_SECONDS=30 -e GP_LIMIT_ROWS=10000 -e GP_CONNECT_TIMEOUT=5 \
  -e PORT=8000 -e HOST=127.0.0.1 \
  --entrypoint python "$image" /test_server.py
