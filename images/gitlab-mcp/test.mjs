// A test of the image gitlab-mcp (FTR.INFRA.MCPS-0001 R2, R3, R11): not root, a read-only root
// filesystem, 401 without a token, 200 with the token of a user against a stub of GitLab, and no
// write tool in tools/list. The stub answers /api/v4/user, the only call the server makes to
// identify the owner of a token.
import { spawn } from "node:child_process";
import http from "node:http";

const token = "glpat-abcdefghij1234567890";
const fake = http.createServer((req, res) => {
  res.setHeader("Content-Type", "application/json");
  res.end(req.url.startsWith("/api/v4/user") ? JSON.stringify({ id: 1, username: "u1", name: "U1" }) : "[]");
});
await new Promise((r) => fake.listen(9100, "127.0.0.1", r));

const server = spawn("mcp-gitlab", [], { stdio: ["ignore", "pipe", "pipe"] });
let log = "";
server.stdout.on("data", (d) => (log += d));
server.stderr.on("data", (d) => (log += d));

const base = "http://127.0.0.1:8000";
const json = { "Content-Type": "application/json", Accept: "application/json, text/event-stream" };
const init = { jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "t", version: "1" } } };

let up = false;
for (let i = 0; i < 120; i++) {
  try {
    const r = await fetch(`${base}/mcp`, { method: "POST", headers: json, body: "{}" });
    if (r.status) { up = true; break; }
  } catch {}
  await new Promise((r) => setTimeout(r, 500));
}
const fail = async (message) => { server.kill(); fake.close(); console.error(message + "\n" + log.slice(-2000)); process.exit(1); };
if (!up) await fail("the server does not answer");

const anonymous = await fetch(`${base}/mcp`, { method: "POST", headers: json, body: JSON.stringify(init) });
if (anonymous.status !== 401) await fail(`expected 401 without a token, got ${anonymous.status}`);

const headers = { ...json, "Private-Token": token };
const first = await fetch(`${base}/mcp`, { method: "POST", headers, body: JSON.stringify(init) });
if (first.status !== 200) await fail(`expected 200 with a token, got ${first.status}`);
const session = first.headers.get("mcp-session-id");
const call = { ...headers, ...(session ? { "mcp-session-id": session } : {}) };
await fetch(`${base}/mcp`, { method: "POST", headers: call, body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) });
const listed = await fetch(`${base}/mcp`, { method: "POST", headers: call, body: JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }) });
const text = await listed.text();
const payload = JSON.parse((text.split("\n").find((l) => l.startsWith("data:")) ?? text).replace(/^data:\s*/, ""));
const names = (payload.result?.tools ?? []).map((t) => t.name);
const write = names.filter((n) => /^(create|update|delete|save|add|remove|accept|approve|merge|push|fork|retry|play|cancel|edit|move)/.test(n));
if (!names.length) await fail("tools/list is empty");
if (write.length) await fail(`write tools are exposed: ${write.join(", ")}`);
console.log(`gitlab-mcp: ok, 401 without a token, ${names.length} read-only tools`);
server.kill();
fake.close();
