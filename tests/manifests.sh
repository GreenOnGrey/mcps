#!/usr/bin/env bash
# Static checks of the chart mcp-servers (FTR.NAB.CMN-0006): a release with each option
# separately and with all together; kubeconform, requests and limits, security of containers,
# network policies; descriptions of servers files/servers/*.json. Needs: helm, kubeconform, jq, yq.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OUT=${OUT:-$ROOT/.work/manifests}
# shellcheck source=../config/versions.env
source "$ROOT/config/versions.env"
KUBE_VERSION=${K8S_VERSION#v}
CHART=$ROOT/charts/mcp-servers
FAILURES=0
mkdir -p "$OUT"

fail() {
  printf '  ✗ %s\n' "$*"
  FAILURES=$((FAILURES + 1))
}
pass() { printf '  ✓ %s\n' "$*"; }

docs_json() { cat "$@" | yq -o=json -I=0 'select(. != null)'; }

# resources_missing <files…>: containers without requests or limits.
resources_missing() {
  docs_json "$@" | jq -r '
    select(.kind | IN("Deployment", "StatefulSet", "DaemonSet", "Job", "Pod", "CronJob"))
    | (if .kind == "Pod" then .spec elif .kind == "CronJob" then .spec.jobTemplate.spec.template.spec else .spec.template.spec end) as $s
    | "\(.kind)/\(.metadata.name)" as $o
    | (($s.containers // []) + ($s.initContainers // []))[]
    | select((.resources.requests.cpu // "") == "" or (.resources.requests.memory // "") == ""
        or (.resources.limits.cpu // "") == "" or (.resources.limits.memory // "") == "")
    | "\($o)/\(.name)"'
}

kubeconform_file() {
  local out
  if out=$(kubeconform -strict -summary -kubernetes-version "$KUBE_VERSION" -skip CustomResourceDefinition \
    -schema-location default -ignore-missing-schemas "$1" 2>&1); then
    pass "QUAL-02 $2: kubeconform — $(tail -1 <<<"$out")"
  else
    fail "QUAL-02 $2: kubeconform"
    echo "$out" | head -10 | sed 's/^/      /'
  fi
}

# helm_args <enabled servers by valuesKey>: the chart values with these servers.
helm_args() {
  local key on
  for key in docling search yandexMail docgen; do
    on=false
    [[ " $* " == *" $key "* ]] && on=true
    printf '%s\n' --set "$key.enabled=$on"
  done
  printf '%s\n' --set-string s3.namespace=storages
}

mcp_chart() {
  printf '\nChart mcp-servers\n'
  local combo dir f keys args=() missing deps want bad pols docs_pols
  for combo in docling search yandex-mail docgen all; do
    case $combo in
      docling) keys=docling want="docling-mcp docling-serve " ;;
      search) keys=search want="search " ;;
      yandex-mail) keys=yandexMail want="yandex-mail " ;;
      docgen) keys=docgen want="doc-convert docgen " ;;
      all) keys="docling search yandexMail docgen" want="doc-convert docgen docling-mcp docling-serve search yandex-mail " ;;
    esac
    dir=$OUT/mcp-$combo
    mkdir -p "$dir"
    f=$dir/mcp-servers.yaml
    # shellcheck disable=SC2086
    mapfile -t args < <(helm_args $keys)
    if ! helm template mcp-servers "$CHART" -n mcp --kube-version "$KUBE_VERSION" "${args[@]}" >"$f" 2>&1; then
      fail "STD-02 $combo: the chart mcp-servers does not render: $(tail -1 "$f")"
      continue
    fi
    kubeconform_file "$f" "mcp-servers $combo"
    missing=$(resources_missing "$f")
    if [[ -z $missing ]]; then pass "RES-01 mcp-servers $combo: requests and limits are set"; else
      fail "RES-01 mcp-servers $combo: no requests or limits: $(echo "$missing" | tr '\n' ' ')"
    fi
    deps=$(docs_json "$f" | jq -r 'select(.kind == "Deployment") | .metadata.name' | sort | tr '\n' ' ')
    if [[ $deps == "$want" ]]; then pass "STD-02 $combo: only $want"; else fail "STD-02 $combo: Deployments '$deps', expected '$want'"; fi
    bad=$(docs_json "$f" | jq -r 'select(.kind == "Deployment") | .metadata.name as $d | .spec.template.spec as $s
      | select($s.automountServiceAccountToken != false or $s.securityContext.runAsNonRoot != true) // empty | $d,
      ($s.containers[] | select(.securityContext.allowPrivilegeEscalation != false or .securityContext.runAsNonRoot != true) | "\($d)/\(.name)")')
    if [[ -z $bad ]]; then pass "SEC-06 $combo: not root, no ServiceAccount token"; else fail "SEC-06 $combo: $(echo "$bad" | tr '\n' ' ')"; fi
    pols=$(docs_json "$f" | jq -r 'select(.kind == "NetworkPolicy") | .metadata.name' | sort | tr '\n' ' ')
    # policies of the document service go only with it
    docs_pols=true
    if [[ $combo == docgen || $combo == all ]] && [[ $pols != *doc-convert-in* || $pols != *egress-docgen* ]]; then docs_pols=false; fi
    if [[ $pols == *default-deny* && $pols == *from-nabu* && $pols == *egress-internet* && $pols == *docling-serve-in* ]] && $docs_pols; then
      pass "SEC-05 $combo: network policies are in place"
    else
      fail "SEC-05 $combo: policies '$pols'"
    fi
    if docs_json "$f" | jq -e 'select(.kind == "NetworkPolicy" and .metadata.name == "egress-internet")
        | .spec.egress[0].to[0].ipBlock.except | (index("10.0.0.0/8") != null and index("169.254.0.0/16") != null)' >/dev/null; then
      pass "SEC-04 $combo: egress to the internet without private networks and cloud metadata"
    else
      fail "SEC-04 $combo: egress-internet does not exclude private networks"
    fi
    if [[ $combo == yandex-mail ]] || grep -Eq '(mcp-[a-z-]+-key|searxng-secret)' "$f" && ! grep -Eq 'value: "?[0-9a-f]{64}' "$f"; then
      pass "STD-14 $combo: keys only from secrets"
    else
      fail "STD-14 $combo: keys not from secrets"
    fi
  done
  if docs_json "$OUT/mcp-search/mcp-servers.yaml" | jq -r 'select(.kind == "ConfigMap" and .metadata.name == "searxng-settings") | .data["settings.yml"]' |
    yq -e '.server.limiter == false and (.use_default_settings.engines.keep_only | length) == 7 and (.use_default_settings.engines.keep_only | contains(["google"]) | not)
      and ([.engines[] | select(.disabled == false) | .name] | join(",")) == (.use_default_settings.engines.keep_only | join(","))' >/dev/null; then
    pass "SRC-06: SearXNG: the listed engines are on explicitly, no Google, the limiter is off"
  else
    fail "SRC-06: settings of SearXNG"
  fi
}

# The installer reads descriptions of servers: fields, the switch in the chart, Deployments and the service.
descriptions() {
  printf '\nDescriptions of servers\n'
  local d name key
  for d in "$CHART"/files/servers/*.json; do
    name=$(basename "$d" .json)
    if ! jq -e --arg n "$name" '.name == $n and (.valuesKey | type) == "string" and (.service | test("^[a-z0-9-]+:[0-9]+$"))
        and (.deployments | type == "array" and length > 0) and (.secrets | type) == "array" and (.wantTools | type) == "array"
        and (.keySecret == null or (.keySecret as $k | .secrets | index($k) != null))' "$d" >/dev/null; then
      fail "DESC $name: an incomplete description"
      continue
    fi
    key=$(jq -r .valuesKey "$d")
    if [[ $(yq -r ".$key.enabled" "$CHART/values.yaml") != false ]]; then
      fail "DESC $name: values.yaml has no $key.enabled: false"
      continue
    fi
    local dep missing_dep=""
    for dep in $(jq -r '.deployments[]' "$d"); do
      docs_json "$OUT/mcp-all/mcp-servers.yaml" | jq -e --arg d "$dep" 'select(.kind == "Deployment" and .metadata.name == $d)' >/dev/null || missing_dep+="$dep "
    done
    if [[ -n $missing_dep ]]; then fail "DESC $name: the chart has no Deployment $missing_dep"; else pass "DESC $name: the description agrees with the chart"; fi
  done
}

# Tags of our own images in the chart values and in config/versions.env match.
image_tags() {
  printf '\nVersions of images\n'
  local pair image version
  for pair in "docling-mcp=$DOCLING_MCP_VERSION" "imap-mcp=$IMAP_MCP_VERSION" "docgen-mcp=$DOCGEN_MCP_VERSION" "doc-convert=$DOC_CONVERT_VERSION"; do
    image=${pair%%=*} version=${pair#*=}
    if grep -q "ghcr.io/greenongrey/$image:$version\$" "$CHART/values.yaml"; then
      pass "VER $image: $version"
    else
      fail "VER $image: values.yaml does not have $version"
    fi
  done
}

mcp_chart
descriptions
image_tags
if ((FAILURES)); then
  printf '\nFailed checks: %d\n' "$FAILURES"
  exit 1
fi
printf '\nAll checks of the chart passed\n'
