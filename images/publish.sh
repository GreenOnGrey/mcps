#!/usr/bin/env bash
# Образы MCP-серверов без официальных образов (FTR.NAB.CMN-0006 tech §2.7):
# сборка, тест образа (images/<образ>/test.sh), сканирование Trivy, публикация.
#   images/publish.sh <реестр>      # релиз: ghcr.io/<организация>; токен — GH_TOKEN
#   images/publish.sh --test-only   # CI: сборка и тесты без публикации
# Тег образа — версия пакета из config/versions.env; опубликованный тег не пересобирается.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
set -a
# shellcheck source=../config/versions.env
source "$ROOT/config/versions.env"
set +a

registry=${1:?реестр или --test-only}
test_only=0
[[ $registry == --test-only ]] && test_only=1 registry=local

if ((!test_only)); then
  printf '%s' "${GH_TOKEN:?нужен GH_TOKEN}" | docker login ghcr.io -u "${GITHUB_ACTOR:-greenongrey}" --password-stdin >/dev/null
fi

# imap-mcp — собственный сервер: собирается из исходников каталога, версия — IMAP_MCP_VERSION.
# doc-convert собирается до docgen-mcp: тест docgen-mcp идёт вместе с ним (FTR.NAB.CHAT-0003).
declare -A VERSIONS=([docling-mcp]=$DOCLING_MCP_VERSION [imap-mcp]=$IMAP_MCP_VERSION [doc-convert]=$DOC_CONVERT_VERSION
  [docgen-mcp]=$DOCGEN_MCP_VERSION)
for name in docling-mcp imap-mcp doc-convert docgen-mcp; do
  version=${VERSIONS[$name]}
  image=$registry/$name:$version
  if ((!test_only)) && docker manifest inspect "$image" >/dev/null 2>&1; then
    echo "- $image — уже опубликован" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
    continue
  fi
  docker build --pull -t "$image" --build-arg "VERSION=$version" --build-arg "GOTENBERG_IMAGE=$GOTENBERG_IMAGE" "$ROOT/images/$name"
  if [[ $name == docgen-mcp ]]; then
    bash "$ROOT/images/$name/test.sh" "$image" "$registry/doc-convert:$DOC_CONVERT_VERSION"
  else
    bash "$ROOT/images/$name/test.sh" "$image"
  fi
  # исправимые HIGH/CRITICAL блокируют выпуск, как у образов Nabu; принятые
  # находки — в images/<образ>/.trivyignore.yaml, каждая с причиной и сроком
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
