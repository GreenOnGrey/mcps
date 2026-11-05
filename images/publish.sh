#!/usr/bin/env bash
# Images of MCP servers without official images (FTR.NAB.CMN-0006 tech §2.7):
# a build, a test of the image (images/<image>/test.sh), a Trivy scan, publishing.
#   images/publish.sh <registry>    # a release: ghcr.io/<organization>; the token is GH_TOKEN
#   images/publish.sh --test-only   # CI: a build and tests without publishing
# The tag of an image is the version of a package from config/versions.env; a published tag is not rebuilt.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
set -a
# shellcheck source=../config/versions.env
source "$ROOT/config/versions.env"
set +a

registry=${1:?a registry or --test-only}
test_only=0
[[ $registry == --test-only ]] && test_only=1 registry=local

if ((!test_only)); then
  printf '%s' "${GH_TOKEN:?GH_TOKEN is required}" | docker login ghcr.io -u "${GITHUB_ACTOR:-greenongrey}" --password-stdin >/dev/null
fi

# imap-mcp is our own server: it builds from the sources of its directory, the version is IMAP_MCP_VERSION.
# doc-convert builds before docgen-mcp: the test of docgen-mcp runs together with it (FTR.NAB.CHAT-0003).
declare -A VERSIONS=([docling-mcp]=$DOCLING_MCP_VERSION [imap-mcp]=$IMAP_MCP_VERSION [doc-convert]=$DOC_CONVERT_VERSION
  [docgen-mcp]=$DOCGEN_MCP_VERSION)
for name in docling-mcp imap-mcp doc-convert docgen-mcp; do
  version=${VERSIONS[$name]}
  image=$registry/$name:$version
  if ((!test_only)) && docker manifest inspect "$image" >/dev/null 2>&1; then
    echo "- $image is already published" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
    continue
  fi
  docker build --pull -t "$image" --build-arg "VERSION=$version" --build-arg "GOTENBERG_IMAGE=$GOTENBERG_IMAGE" "$ROOT/images/$name"
  if [[ $name == docgen-mcp ]]; then
    bash "$ROOT/images/$name/test.sh" "$image" "$registry/doc-convert:$DOC_CONVERT_VERSION"
  else
    bash "$ROOT/images/$name/test.sh" "$image"
  fi
  # fixable HIGH/CRITICAL block a release, as with the images of Nabu; accepted
  # findings are in images/<image>/.trivyignore.yaml, each with a reason and a deadline
  ignore=()
  if [[ -f $ROOT/images/$name/.trivyignore.yaml ]]; then
    ignore=(-v "$ROOT/images/$name/.trivyignore.yaml:/trivyignore.yaml:ro")
  fi
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock "${ignore[@]}" "$TRIVY_IMAGE" image --quiet \
    --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 ${ignore:+--ignorefile /trivyignore.yaml} "$image"
  if ((!test_only)); then
    docker push "$image"
    echo "- $image" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
  fi
done
