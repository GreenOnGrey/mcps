#!/usr/bin/env bash
# A test of the image gitlab-mcp (FTR.INFRA.MCPS-0001 R2, R3, R11): the checks are in test.mjs;
# this script starts the container with the environment of the chart.
set -euo pipefail
image=${1:?the image}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
docker run --rm --read-only --tmpfs /tmp -u 10001 \
  -v "$here/test.mjs:/test.mjs:ro" \
  -e STREAMABLE_HTTP=true -e REMOTE_AUTHORIZATION=true \
  -e GITLAB_API_URL=http://127.0.0.1:9100/api/v4 -e GITLAB_ALLOWED_HOSTS=127.0.0.1 \
  -e GITLAB_PERMISSION_MODE=readonly \
  -e GITLAB_TOOLSETS=core,merge_requests,repositories,issues,ci,pipelines,projects,branches,search \
  -e GITLAB_DOWNLOAD_DIR=/tmp/downloads -e PORT=8000 -e HOST=127.0.0.1 \
  --entrypoint node "$image" /test.mjs
