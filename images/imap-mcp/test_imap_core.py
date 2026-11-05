"""Tests of the read-only IMAP core (FTR.NAB.CMN-0006 YML-*): python -m unittest."""

import io
import json
import unittest
import urllib.error

import imap_core as core

RAW = (
    b"From: =?utf-8?b?0JjQstCw0L0=?= <ivan@example.ru>\r\n"
    b"To: me@yandex.ru, Petr <petr@example.ru>\r\n"
    b"Subject: =?utf-8?b?0J/QvtGB0YLQsNCy0LrQsA==?=\r\n"
    b"Date: Tue, 13 Oct 2026 10:15:00 +0300\r\n"
    b"Message-ID: <m1@example.ru>\r\n"
    b"References: <root@example.ru> <m0@example.ru>\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: multipart/mixed; boundary=b\r\n\r\n"
    b"--b\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<p>\xd0\xa1\xd1\x80\xd0\xbe\xd0\xba &amp; 15.11</p><script>x()</script>\r\n"
    b"--b\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=\"a.pdf\"\r\n"
    b"Content-Transfer-Encoding: base64\r\n\r\nJVBERi0=\r\n--b--\r\n"
)
HEAD = RAW.split(b"MIME-Version")[0] + b"\r\n"


class FakeIMAP:
    """Records commands; answers as a server with one message, uid 7."""

    def __init__(self, folders=None, auth_error=None):
        self.cmds = []
        self.literal = None
        self.selected = None
        self.folders = folders or [b'(\\HasNoChildren) "|" "INBOX"', b'(\\HasNoChildren \\Sent) "|" "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"',
                                   b'(\\Noselect \\HasChildren) "|" "Archive"']
        self.auth_error = auth_error

    def authenticate(self, mech, cb):
        self.cmds.append(("AUTHENTICATE", mech, cb(b"")))
        if self.auth_error:
            raise core.imaplib.IMAP4.error(self.auth_error)

    def shutdown(self):
        pass

    def list(self):
        return "OK", list(self.folders)

    def select(self, mailbox, readonly=False):
        self.cmds.append(("SELECT", mailbox, readonly))
        self.selected = mailbox
        return ("OK", [b"1"]) if mailbox in ('"INBOX"', '"&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"') else ("NO", [b"no such folder"])

    def uid(self, cmd, *args):
        self.cmds.append(("UID", cmd, args, self.literal))
        self.literal = None
        if cmd == "SEARCH":
            return "OK", [b"3 7"]
        if cmd == "FETCH":
            what = args[1]
            if "HEADER.FIELDS" in what:
                return "OK", [(b"1 (UID 7 FLAGS () RFC822.SIZE 900 BODY[HEADER.FIELDS (...)] {10}", HEAD), b")",
                              (b"2 (UID 3 FLAGS (\\Seen) RFC822.SIZE 100 BODY[HEADER.FIELDS (...)] {10}", b"Subject: other\r\nFrom: a@b.c\r\n\r\n"), b")"]
            return "OK", [(b"1 (UID 7 BODY[] {900}", RAW), b")"]
        raise AssertionError(cmd)


class Core(unittest.TestCase):
    def test_folder_names(self):
        self.assertEqual(core.decode_folder("&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"), "Отправленные")
        self.assertEqual(core.encode_folder("Отправленные"), "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-")
        self.assertEqual(core.decode_folder(core.encode_folder("R&D/Проект")), "R&D/Проект")

    def test_list_folders_skips_noselect(self):
        folders = core.list_folders(FakeIMAP())
        self.assertEqual([f["name"] for f in folders], ["INBOX", "Отправленные"])
        self.assertEqual(folders[1]["special"], "Sent")

    def test_xoauth2(self):
        conn = FakeIMAP()
        core.connect("me@yandex.ru", "tok", factory=lambda: conn)
        self.assertEqual(conn.cmds[0][2], b"user=me@yandex.ru\x01auth=Bearer tok\x01\x01")

    def test_imap_disabled_is_explained(self):
        with self.assertRaises(core.MailError) as e:
            core.connect("me@yandex.ru", "tok", factory=lambda: FakeIMAP(auth_error="AUTHENTICATE Invalid credentials or IMAP is disabled"))
        self.assertIn("Mail clients", str(e.exception))

    def test_bad_token_is_auth_error(self):
        with self.assertRaises(core.AuthError):
            core.connect("me@yandex.ru", "tok", factory=lambda: FakeIMAP(auth_error="AUTHENTICATE failed"))

    def test_search_read_only_and_newest_first(self):
        conn = FakeIMAP()
        out = core.search(conn, core.Query(sender="ivan@example.ru", since="2026-10-01", limit=5))
        self.assertEqual(conn.cmds[0], ("SELECT", '"INBOX"', True))  # EXAMINE
        search = conn.cmds[1]
        self.assertEqual(search[2], ("SINCE", "01-Oct-2026", "FROM", '"ivan@example.ru"'))
        self.assertEqual([m["uid"] for m in out], [7, 3])
        self.assertEqual(out[0]["subject"], "Поставка")
        self.assertEqual(out[0]["from"], "ivan@example.ru")
        self.assertEqual(out[0]["to"], ["me@yandex.ru", "petr@example.ru"])
        self.assertFalse(out[0]["seen"])
        self.assertTrue(out[1]["seen"])
        fetch = conn.cmds[2]
        self.assertIn("BODY.PEEK[HEADER.FIELDS", fetch[2][1])

    def test_search_non_latin_goes_as_one_literal(self):
        conn = FakeIMAP()
        out = core.search(conn, core.Query(subject="Поставка", sender="Иван"))
        search = conn.cmds[1]
        self.assertEqual(search[2][:2], ("CHARSET", "UTF-8"))
        self.assertEqual(search[2][-1], "FROM")
        self.assertEqual(search[3], "Иван".encode())
        # the second non-Latin condition is checked against decoded headers
        self.assertEqual([m["uid"] for m in out], [7])

    def test_bad_date(self):
        with self.assertRaises(core.MailError):
            core.search(FakeIMAP(), core.Query(since="13.10.2026"))

    def test_unknown_folder(self):
        with self.assertRaises(core.MailError):
            core.search(FakeIMAP(), core.Query(folder="Нет такой"))

    def test_read_message_peeks_and_lists_attachments(self):
        conn = FakeIMAP()
        m = core.read_message(conn, "INBOX", 7)
        self.assertTrue(all(c[0] != "UID" or c[1] != "STORE" for c in conn.cmds))
        self.assertIn("(BODY.PEEK[])", conn.cmds[-1][2])
        self.assertEqual(m["text"], "Срок & 15.11")
        self.assertEqual(m["attachments"], [{"name": "a.pdf", "contentType": "application/pdf", "size": 5}])
        self.assertFalse(m["truncated"])

    def test_read_large_message_only_headers(self):
        old = core.MAX_MESSAGE_BYTES
        core.MAX_MESSAGE_BYTES = 100
        try:
            conn = FakeIMAP()
            m = core.read_message(conn, "INBOX", 7)
            self.assertTrue(m["truncated"])
            self.assertFalse(any("BODY.PEEK[]" in str(c) for c in conn.cmds))
        finally:
            core.MAX_MESSAGE_BYTES = old


class Threads(unittest.TestCase):
    def test_search_gives_thread_bulk_and_link(self):
        out = core.search(FakeIMAP(), core.Query())
        self.assertEqual(out[0]["threadId"], "<root@example.ru>")
        self.assertFalse(out[0]["bulk"])
        self.assertEqual(out[0]["webLink"], "https://mail.yandex.ru/#search?request=m1%40example.ru")

    def test_bulk_by_headers(self):
        msg = core.email.message_from_bytes(b"List-Unsubscribe: <mailto:x@y>\r\nFrom: a@b.c\r\n\r\n")
        self.assertTrue(core.bulk(msg))
        self.assertTrue(core.bulk(core.email.message_from_bytes(b"Auto-Submitted: auto-replied\r\n\r\n")))
        self.assertFalse(core.bulk(core.email.message_from_bytes(b"Auto-Submitted: no\r\n\r\n")))

    def test_thread_id_without_references(self):
        msg = core.email.message_from_bytes(b"Message-ID: <solo@x>\r\n\r\n")
        self.assertEqual(core.thread_id(msg), "<solo@x>")
        msg = core.email.message_from_bytes(b"Message-ID: <b@x>\r\nIn-Reply-To: <a@x>\r\n\r\n")
        self.assertEqual(core.thread_id(msg), "<a@x>")

    def test_offset_pages(self):
        conn = FakeIMAP()
        self.assertEqual([m["uid"] for m in core.search(conn, core.Query(offset=1))], [3])

    def test_read_thread_searches_folders_and_peeks(self):
        conn = FakeIMAP()
        t = core.read_thread(conn, "<root@example.ru>")
        searches = [c for c in conn.cmds if c[0] == "UID" and c[1] == "SEARCH"]
        self.assertEqual(searches[0][2], ("OR", "HEADER", "Message-ID", '"<root@example.ru>"', "HEADER", "References", '"<root@example.ru>"'))
        self.assertEqual(len(searches), 2)  # INBOX и «Отправленные»; Archive — \\Noselect
        self.assertEqual(t["threadId"], "<root@example.ru>")
        self.assertTrue(all("BODY.PEEK" in str(c) for c in conn.cmds if c[0] == "UID" and c[1] == "FETCH"))
        # the same message from two folders comes once (a message without a Message-ID cannot be identified)
        ids = [m["messageId"] for m in t["messages"] if m["messageId"]]
        self.assertEqual(ids, ["<m1@example.ru>"])

    def test_read_thread_without_header_search(self):
        """Yandex does not search by headers: messages from the date since, the thread by their headers."""
        class NoHeaderSearch(FakeIMAP):
            def uid(self, cmd, *args):
                if cmd == "SEARCH" and "HEADER" in args:
                    self.cmds.append(("UID", cmd, args, None))
                    return "NO", [b"[UNAVAILABLE] UID SEARCH Backend error. sc=x"]
                return super().uid(cmd, *args)

        conn = NoHeaderSearch()
        t = core.read_thread(conn, "<root@example.ru>", since="2026-10-12")
        searches = [c[2] for c in conn.cmds if c[0] == "UID" and c[1] == "SEARCH"]
        self.assertIn(("SINCE", "12-Oct-2026"), searches)
        # uid 3, a message of another thread, is not included
        self.assertEqual([(m["uid"], m["messageId"]) for m in t["messages"]], [(7, "<m1@example.ru>")])
        with self.assertRaises(core.MailError):
            core.read_thread(NoHeaderSearch(), "<root@example.ru>", since="12.10.2026")

    def test_read_thread_needs_message_id(self):
        with self.assertRaises(core.MailError):
            core.read_thread(FakeIMAP(), "subject words")

    def test_all_mail_is_enough(self):
        conn = FakeIMAP(folders=[b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"',
                                 b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"'])
        self.assertEqual(core.thread_folders(conn), ["[Gmail]/All Mail"])


class Resolve(unittest.TestCase):
    def setUp(self):
        core._cache.clear()

    def test_email_by_token_cached(self):
        calls = []

        def opener(req, timeout):
            calls.append(req.headers["Authorization"])
            return io.BytesIO(json.dumps({"default_email": "me@yandex.ru"}).encode())

        self.assertEqual(core.resolve_email("tok", opener), "me@yandex.ru")
        self.assertEqual(core.resolve_email("tok", opener), "me@yandex.ru")
        self.assertEqual(calls, ["OAuth tok"])
        self.assertNotIn("tok", core._cache)  # в памяти только хеш токена

    def test_refused_token(self):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        with self.assertRaises(core.AuthError):
            core.resolve_email("bad", opener)

    def test_no_token(self):
        with self.assertRaises(core.AuthError):
            core.resolve_email("")


if __name__ == "__main__":
    unittest.main()
