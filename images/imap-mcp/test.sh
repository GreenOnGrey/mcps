#!/usr/bin/env bash
# Тест образа imap-mcp (FTR.NAB.CMN-0006 YML-01, IMG-03): не root, корневая ФС
# только для чтения, без токена — 401, с токеном проходит до проверки у Яндекса
# (здесь — недоступный сервис: ответ — ошибка, а не 200 с данными).
set -euo pipefail
image=${1:?образ}
docker run --rm --read-only --tmpfs /tmp -e PORT=8000 -e USERINFO_URL=http://127.0.0.1:9/info \
  --entrypoint python "$image" -c '
import json, os, subprocess, sys, time, urllib.error, urllib.request
assert os.getuid() != 0, "runs as root"
p = subprocess.Popen([sys.executable, "/app/server.py"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}).encode()
h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
def call(extra):
    try:
        return urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8000/mcp", body, {**h, **extra}), timeout=10).status
    except urllib.error.HTTPError as e:
        return e.code
for _ in range(60):
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=2)
        break
    except Exception:
        time.sleep(0.5)
codes = (call({}), call({"Authorization": "Bearer x"}))
p.terminate()
out = p.communicate(timeout=10)[0]
if codes[0] != 401 or codes[1] == 200:
    sys.exit(f"unexpected answers {codes}\n{out[-2000:]}")
print("imap-mcp: ok, 401 without a token")
'
