#!/usr/bin/env bash
# A test of the image search-mcp (FTR.INFRA.MCPS-0003 R2–R8, R13): the checks are in test_server.py;
# this script runs them inside the container with the environment of the chart. The vendor is a fake
# on loopback, so the test needs no key and no network.
set -euo pipefail
image=${1:?the image}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
docker run --rm --read-only --tmpfs /tmp -u 10001 \
  -v "$here/test_server.py:/test_server.py:ro" \
  -e SEARCH_KEY= -e SEARCH_URL= -e SEARCH_TIMEOUT_SECONDS=30 -e SEARCH_MAX_CONTENT_BYTES=102400 \
  -e SEARCH_RETRIES=2 -e PORT=8000 -e HOST=127.0.0.1 \
  --entrypoint python "$image" /test_server.py
