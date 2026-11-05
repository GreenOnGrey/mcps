"""Tests of the HTTP side of imap-mcp (FTR.NAB.CMN-0006 YML-01): python -m unittest."""

import json
import unittest

from starlette.testclient import TestClient

import imap_core as core
import server

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}


class FakeConn:
    def __init__(self):
        self.closed = False

    def logout(self):
        self.closed = True


class Server(unittest.TestCase):
    def setUp(self):
        self.seen = []
        self.orig = (core.resolve_email, core.connect, core.list_folders)

        def resolve(token, opener=None):
            if token != "good":
                raise core.AuthError("refused")
            return "me@yandex.ru"

        def connect(address, token, factory=None):
            self.seen.append((address, token))
            return FakeConn()

        core.resolve_email, core.connect = resolve, connect
        core.list_folders = lambda conn: [{"name": "INBOX", "special": "Inbox"}]

    def tearDown(self):
        core.resolve_email, core.connect, core.list_folders = self.orig

    def client(self):
        return TestClient(server.build_app())

    def test_health_without_token(self):
        with self.client() as c:
            self.assertEqual(c.get("/healthz").status_code, 200)

    def test_401_without_or_with_bad_token(self):
        with self.client() as c:
            self.assertEqual(c.post("/mcp", json=INIT, headers=H).status_code, 401)
            self.assertEqual(c.post("/mcp", json=INIT, headers={**H, "Authorization": "Bearer bad"}).status_code, 401)

    def test_tools_read_only_and_token_reaches_imap(self):
        auth = {**H, "Authorization": "Bearer good", "Host": "yandex-mail.mcp.svc:8000"}
        with self.client() as c:
            self.assertEqual(c.post("/mcp", json=INIT, headers=auth).status_code, 200)
            tools = c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers=auth).json()
            names = {t["name"]: t["annotations"]["readOnlyHint"] for t in tools["result"]["tools"]}
            self.assertEqual(names, {"list_folders": True, "search_messages": True, "read_message": True, "read_thread": True})
            res = c.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                       "params": {"name": "list_folders", "arguments": {}}}, headers=auth).json()
        self.assertFalse(res["result"].get("isError"))
        self.assertEqual(json.loads(res["result"]["content"][0]["text"]), [{"name": "INBOX", "special": "Inbox"}])
        self.assertEqual(self.seen, [("me@yandex.ru", "good")])

    def test_imap_refusal_is_401(self):
        """YML-01: the address service took the token, but IMAP refused it: 401 too."""
        def refused(address, token, factory=None):
            raise core.AuthError("AUTHENTICATE failed")

        core.connect = refused
        auth = {**H, "Authorization": "Bearer good"}
        with self.client() as c:
            r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                     "params": {"name": "list_folders", "arguments": {}}}, headers=auth)
        self.assertEqual(r.status_code, 401)

    def call_list_folders(self):
        auth = {**H, "Authorization": "Bearer good"}
        with self.client() as c:
            return c.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                        "params": {"name": "list_folders", "arguments": {}}}, headers=auth).json()["result"]

    def test_dropped_connection_is_retried_once(self):
        """The server drops the connection: one repeat on a new connection."""
        calls = []

        def flaky(conn):
            calls.append(conn)
            if len(calls) == 1:
                raise core.imaplib.IMAP4.abort("command: SEARCH => socket error: EOF")
            return [{"name": "INBOX", "special": "Inbox"}]

        core.list_folders = flaky
        res = self.call_list_folders()
        self.assertFalse(res.get("isError"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.seen), 2)

    def test_failure_reason_without_mailbox_data(self):
        """A second drop gives an error with a reason; a line of the server answer does not reach it."""
        def broken(conn):
            raise core.imaplib.IMAP4.abort("unexpected response: '* 5 FETCH (Subject: secret)'")

        core.list_folders = broken
        with self.assertLogs("imap-mcp", "WARNING") as logs:
            res = self.call_list_folders()
        self.assertTrue(res.get("isError"))
        text = res["content"][0]["text"]
        self.assertIn("abort: unexpected response", text)
        self.assertNotIn("secret", text + " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
