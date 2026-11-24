#!/usr/bin/env bash
# Static checks of the local run (FTR.INFRA.MCPS-0002 R14): every server of the project is in the
# file, profiles choose the servers explicitly, every service has a healthcheck and a restart policy,
# our own images are hardened, and the file carries no credential of a system.
# Needs: docker with the compose plugin, jq.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
FAILURES=0
# The servers of the project: images/ and files/servers/. A new server has to appear here as well.
EXPECTED_ALL="clickhouse confluence doc-convert docgen docling docling-serve gitlab greenplum jira search searxng yandex-mail"

fail() { printf '  ✗ %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
pass() { printf '  ✓ %s\n' "$*"; }

printf '\nLocal run (docker compose)\n'

if ! docker compose version >/dev/null 2>&1; then
  fail "COMPOSE-01 docker compose is not available"
  printf '\nFailed checks: %d\n' "$FAILURES"
  exit 1
fi
pass "COMPOSE-01 docker compose is available"

services=$(cd "$ROOT" && docker compose config --services 2>/dev/null | tr '\n' ' ')
if [[ -z ${services// /} ]]; then
  pass "COMPOSE-02 no profile — no servers"
else
  fail "COMPOSE-02 without a profile the services are '$services'"
fi

greenplum=$(cd "$ROOT" && docker compose --profile greenplum config --services 2>/dev/null | tr '\n' ' ')
if [[ $greenplum == "greenplum " ]]; then
  pass "COMPOSE-03 the profile greenplum starts only its server"
else
  fail "COMPOSE-03 the profile greenplum starts '$greenplum'"
fi

all=$(cd "$ROOT" && docker compose --profile all config --services 2>/dev/null | sort | tr '\n' ' ')
if [[ $all == "$EXPECTED_ALL " ]]; then
  pass "COMPOSE-04 the profile all starts every server of the project"
else
  fail "COMPOSE-04 the profile all starts '$all', expected '$EXPECTED_ALL '"
fi

cfg=$(cd "$ROOT" && docker compose --profile all config --format json 2>/dev/null)

if jq -e 'all(.services[]; (.healthcheck != null) and (.restart == "unless-stopped") and (.security_opt | index("no-new-privileges:true") != null))' <<<"$cfg" >/dev/null 2>&1; then
  pass "COMPOSE-05 every service has a healthcheck, a restart policy and no new privileges"
else
  fail "COMPOSE-05 a service without a healthcheck, a restart policy or no-new-privileges"
fi

if jq -e 'all(.services[] | select(.build != null); (.read_only == true) and (.user == "10001"))' <<<"$cfg" >/dev/null 2>&1; then
  pass "COMPOSE-06 our own images run read-only and as a non-root user"
else
  fail "COMPOSE-06 an own image runs writable or as root"
fi

ports=$(jq -r '[.services[].ports[]?.published] | map(select(. != null)) | .[]' <<<"$cfg" | sort)
if [[ $(echo "$ports" | uniq -d) == "" ]]; then
  pass "COMPOSE-07 host ports are unique"
else
  fail "COMPOSE-07 the host ports repeat: $(echo "$ports" | uniq -d | tr '\n' ' ')"
fi

if grep -Eqi '(password|token|secret)[[:space:]]*[:=][[:space:]]*[^ $]' "$ROOT/.env.example" 2>/dev/null; then
  fail "COMPOSE-08 .env.example carries a credential"
else
  pass "COMPOSE-08 .env.example carries no credential"
fi

if ((FAILURES)); then
  printf '\nFailed checks: %d\n' "$FAILURES"
  exit 1
fi
printf '\nAll checks of the local run passed\n'
