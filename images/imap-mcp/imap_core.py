"""Reading a mailbox over IMAP with the OAuth token of its owner.

FTR.NAB.CMN-0006 R20–R22: the token comes with each request (added by
the MCP proxy of Nabu), the address of the mailbox comes from the same token, and nothing is
stored. Folders open with the command EXAMINE, and bodies of messages are read through
BODY.PEEK, so the flags of messages do not change; the scope mail:imap_ro forbids changes
on the side of Yandex too.

Texts of errors are for the agent, so they are in English, like the answers of the Nabu proxy.
"""

from __future__ import annotations

import base64
import datetime
import email
import email.header
import email.policy
import email.utils
import hashlib
import html
import imaplib
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message

IMAP_HOST = os.environ.get("IMAP_HOST", "imap.yandex.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
# Where the address of the mailbox comes from: GET with the token, a JSON field of the answer.
USERINFO_URL = os.environ.get("USERINFO_URL", "https://login.yandex.ru/info?format=json")
USERINFO_AUTH = os.environ.get("USERINFO_AUTH", "OAuth")  # "OAuth <token>" at Yandex, "Bearer" at others
USERINFO_FIELD = os.environ.get("USERINFO_FIELD", "default_email")
TIMEOUT = float(os.environ.get("IMAP_TIMEOUT", "30"))
MAX_RESULTS = 50
MAX_MESSAGE_BYTES = int(os.environ.get("MAX_MESSAGE_BYTES", str(25 << 20)))
CACHE_TTL = 300.0

HEADER_FIELDS = "FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES LIST-UNSUBSCRIBE LIST-ID PRECEDENCE AUTO-SUBMITTED"
# The link to a message in the web interface of the provider: {msgid} is the Message-ID without angle
# brackets (FTR.NAB.CMN-0003 R6: a note leads to the message). Empty means no link.
WEB_LINK = os.environ.get("WEB_LINK", "https://mail.yandex.ru/#search?request={msgid}")
# Folders from which neither a mailbox search nor threads read.
SKIP_ROLES = {"Trash", "Junk", "Spam", "Drafts"}
# A thread on a server without a search by headers: messages of THREAD_DAYS days (without
# since), at most THREAD_SCAN newest in a folder, headers by THREAD_CHUNK.
THREAD_DAYS = int(os.environ.get("THREAD_DAYS", "90"))
THREAD_SCAN = 3000
THREAD_CHUNK = 200


class AuthError(Exception):
    """No token, or it has expired or been revoked: the HTTP answer is 401."""


class MailError(Exception):
    """An error for the agent: the text explains what to do."""


# --- the address of the mailbox by the token --------------------------------

_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_email(token: str, opener=urllib.request.urlopen) -> str:
    """The address of the owner of the token; kept in memory for five minutes by the hash of the token."""
    if not token:
        raise AuthError("no token")
    k = _key(token)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(k)
        if hit and hit[1] > now:
            return hit[0]
    req = urllib.request.Request(USERINFO_URL, headers={"Authorization": f"{USERINFO_AUTH} {token}"})
    try:
        with opener(req, timeout=TIMEOUT) as r:
            info = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError("the token was refused") from None
        raise MailError(f"the account service answered HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise MailError(f"the account service is unreachable: {e}") from None
    address = info.get(USERINFO_FIELD) or ""
    if not address and isinstance(info.get("emails"), list) and info["emails"]:
        address = info["emails"][0]
    if not address:
        raise AuthError(f"the token gives no {USERINFO_FIELD}: grant the scope login:email")
    with _cache_lock:
        if len(_cache) > 10000:
            _cache.clear()
        _cache[k] = (address, now + CACHE_TTL)
    return address


def forget(token: str) -> None:
    with _cache_lock:
        _cache.pop(_key(token), None)


# --- modified UTF-7 of IMAP folder names (RFC 3501 §5.1.3) --------------------

def decode_folder(name: str) -> str:
    out, i = [], 0
    while i < len(name):
        c = name[i]
        if c == "&":
            j = name.index("-", i)
            if j == i + 1:
                out.append("&")
            else:
                b64 = name[i + 1:j].replace(",", "/")
                b64 += "=" * (-len(b64) % 4)
                out.append(base64.b64decode(b64).decode("utf-16-be"))
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def encode_folder(name: str) -> str:
    out, buf = [], []

    def flush():
        if buf:
            b = base64.b64encode("".join(buf).encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            out.append("&" + b + "-")
            buf.clear()

    for c in name:
        if 0x20 <= ord(c) <= 0x7E:
            flush()
            out.append("&-" if c == "&" else c)
        else:
            buf.append(c)
    flush()
    return "".join(out)


def quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- IMAP --------------------------------------------------------------------

def connect(address: str, token: str, factory=None) -> imaplib.IMAP4:
    """A connection after signing in by XOAUTH2 with the token of the owner."""
    if factory is None:
        def factory():
            return imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
    try:
        conn = factory()
    except OSError as e:
        raise MailError(f"the IMAP server {IMAP_HOST} is unreachable: {e}") from None
    auth = f"user={address}\x01auth=Bearer {token}\x01\x01".encode()
    try:
        conn.authenticate("XOAUTH2", lambda _: auth)
    except imaplib.IMAP4.error as e:
        text = str(e)
        try:
            conn.shutdown()
        except Exception:
            pass
        if "disabled" in text.lower():
            raise MailError(
                "IMAP access is disabled for this mailbox. In Yandex Mail open Settings → Mail clients and turn on "
                "access by IMAP with app passwords and OAuth tokens; in Yandex 360 the administrator of the "
                "organization allows mail clients for its users."
            ) from None
        raise AuthError(text) from None
    return conn


def _ok(typ, data, what: str):
    if typ != "OK":
        detail = b" ".join(d for d in data if isinstance(d, bytes)).decode(errors="replace")
        raise MailError(f"{what}: {detail or typ}")
    return data


def _examine(conn, folder: str) -> None:
    typ, data = conn.select(quote(encode_folder(folder)), readonly=True)
    if typ != "OK":
        raise MailError(f"there is no folder {folder!r}; see list_folders")


_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\) (?:"(?P<sep>[^"]*)"|NIL) (?P<name>.+)$')


def list_folders(conn) -> list[dict]:
    data = _ok(*conn.list(), "LIST")
    out = []
    for line in data:
        if isinstance(line, tuple):
            line = line[0] + line[1]
        if not line:
            continue
        m = _LIST_RE.match(line)
        if not m:
            continue
        flags = m.group("flags").decode().split()
        if "\\Noselect" in flags or "\\NonExistent" in flags:
            continue
        raw = m.group("name").decode()
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        special = next((f[1:] for f in flags if f in ("\\Inbox", "\\Sent", "\\Drafts", "\\Trash", "\\Junk", "\\Archive", "\\Spam", "\\All")), None)
        if raw.upper() == "INBOX":
            special = "Inbox"
        out.append({"name": decode_folder(raw), "special": special})
    return out


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except Exception:
        return value


def _addresses(value: str | None) -> list[str]:
    return [a for _, a in email.utils.getaddresses([_decode(value)]) if a]


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).isoformat()
    except Exception:
        return None


def _imap_date(iso: str) -> str:
    y, m, d = (int(x) for x in iso[:10].split("-"))
    return f"{d:02d}-{['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][m - 1]}-{y}"


@dataclass
class Query:
    folder: str = "INBOX"
    sender: str = ""
    recipient: str = ""
    subject: str = ""
    text: str = ""
    since: str = ""   # YYYY-MM-DD
    before: str = ""  # YYYY-MM-DD, exclusive
    unseen: bool = False
    limit: int = 20
    offset: int = 0  # skip this many newest messages: paging


def search(conn, q: Query) -> list[dict]:
    """Messages by a query, newest first, with headers."""
    _examine(conn, q.folder)
    crit: list[str] = []
    for key, iso in (("SINCE", q.since), ("BEFORE", q.before)):
        if iso:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
                raise MailError(f"{key.lower()} is a date YYYY-MM-DD")
            crit += [key, _imap_date(iso)]
    if q.unseen:
        crit.append("UNSEEN")
    # IMAP takes one literal per command: the text (or the first non-Latin
    # condition) goes to the server, and other non-Latin conditions are checked
    # here against decoded headers.
    terms = [("TEXT", q.text), ("FROM", q.sender), ("TO", q.recipient), ("SUBJECT", q.subject)]
    literal, local = None, []
    for key, value in terms:
        if not value:
            continue
        if value.isascii():
            crit += [key, quote(value)]
        elif literal is None:
            literal = (key, value)
        else:
            local.append((key, value.casefold()))
    if literal:
        crit.append(literal[0])
        conn.literal = literal[1].encode()
        typ, data = conn.uid("SEARCH", "CHARSET", "UTF-8", *(crit or ["ALL"]))
    else:
        typ, data = conn.uid("SEARCH", *(crit or ["ALL"]))
    _ok(typ, data, "SEARCH")
    uids = (data[0] or b"").split()
    uids.sort(key=int, reverse=True)
    if not local:
        uids = uids[max(0, int(q.offset or 0)):]
    limit = max(1, min(int(q.limit or 20), MAX_RESULTS))
    out: list[dict] = []
    for chunk_start in range(0, len(uids), limit):
        chunk = uids[chunk_start:chunk_start + limit]
        for item in fetch_headers(conn, chunk):
            if all(v in " ".join(_field(item, k)).casefold() for k, v in local):
                item["folder"] = q.folder
                out.append(item)
                if len(out) >= limit:
                    return out
        if not local:
            break
    return out


def _field(item: dict, key: str) -> list[str]:
    return {"FROM": [item.get("from") or ""], "TO": item.get("to", []) + item.get("cc", []),
            "SUBJECT": [item.get("subject") or ""], "TEXT": [item.get("subject") or ""]}[key]


_FETCH_UID = re.compile(rb"UID (\d+)")
_FETCH_FLAGS = re.compile(rb"FLAGS \(([^)]*)\)")
_FETCH_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")


def fetch_headers(conn, uids: list[bytes]) -> list[dict]:
    if not uids:
        return []
    typ, data = conn.uid("FETCH", b",".join(uids).decode(), f"(UID FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
    _ok(typ, data, "FETCH")
    items = {}
    for part in data:
        if not isinstance(part, tuple):
            continue
        meta, raw = part
        uid = _FETCH_UID.search(meta)
        if not uid:
            continue
        msg = email.message_from_bytes(raw, policy=email.policy.compat32)
        fm = _FETCH_FLAGS.search(meta)
        flags = fm.group(1).decode().split() if fm else []
        size = _FETCH_SIZE.search(meta)
        items[uid.group(1)] = {
            "uid": int(uid.group(1)),
            "from": (_addresses(msg.get("From")) or [_decode(msg.get("From"))])[0],
            "fromName": _decode(email.utils.parseaddr(msg.get("From") or "")[0]),
            "to": _addresses(msg.get("To")),
            "cc": _addresses(msg.get("Cc")),
            "subject": _decode(msg.get("Subject")),
            "date": _date(msg.get("Date")),
            "messageId": (msg.get("Message-ID") or "").strip(),
            "inReplyTo": (msg.get("In-Reply-To") or "").strip(),
            "threadId": thread_id(msg),
            "bulk": bulk(msg),
            "webLink": web_link(msg.get("Message-ID")),
            "seen": "\\Seen" in flags,
            "size": int(size.group(1)) if size else None,
        }
    return [items[u] for u in uids if u in items]


def _text_of(msg: Message) -> tuple[str, list[dict]]:
    plain, htmls, attachments = [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        name = _decode(part.get_filename())
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True) or b""
        if name or disp.startswith("attachment"):
            attachments.append({"name": name or "(no name)", "contentType": ctype, "size": len(payload)})
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if ctype == "text/plain":
            plain.append(text)
        elif ctype == "text/html":
            htmls.append(text)
    if plain:
        return "\n\n".join(plain).strip(), attachments
    return "\n\n".join(html_to_text(h) for h in htmls).strip(), attachments


_TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)
_BREAKS = re.compile(r"<(br|/p|/div|/tr|/li|/h[1-6])\b[^>]*>", re.I)


def html_to_text(s: str) -> str:
    s = _BREAKS.sub("\n", s)
    s = _TAGS.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


_MSGID = re.compile(r"<[^<>\s]+>")


def thread_id(msg: Message) -> str:
    """The thread of a message is the Message-ID of its first message: the first in References, otherwise
    In-Reply-To, otherwise its own (IMAP gives no thread identifier)."""
    for header in ("References", "In-Reply-To", "Message-ID"):
        found = _MSGID.findall(msg.get(header) or "")
        if found:
            return found[0]
    return (msg.get("Message-ID") or "").strip()


def bulk(msg: Message) -> bool:
    """A mailing list or an automatic message, by headers (RFC 2369, 3834)."""
    precedence = (msg.get("Precedence") or "").strip().lower()
    auto = (msg.get("Auto-Submitted") or "no").strip().lower()
    return bool(msg.get("List-Unsubscribe") or msg.get("List-Id")) or precedence in ("bulk", "list", "junk") or auto != "no"


def web_link(message_id: str | None) -> str:
    mid = (message_id or "").strip().strip("<>")
    if not WEB_LINK or not mid:
        return ""
    return WEB_LINK.replace("{msgid}", urllib.parse.quote(mid, safe=""))


def thread_folders(conn) -> list[str]:
    """Folders for a search over the whole mailbox: no trash, spam and drafts. If the
    mailbox has the folder "All Mail" (\\All, Gmail), it alone is enough."""
    folders = list_folders(conn)
    every = [f["name"] for f in folders if f["special"] == "All"]
    return every or [f["name"] for f in folders if f["special"] not in SKIP_ROLES]


def _thread_items(conn, thread: str, since: str) -> list[dict]:
    """Messages of a thread in an open folder. Not all servers have a search by
    headers (Yandex answers "[UNAVAILABLE] Backend error"): then it takes messages
    from the date since (by default THREAD_DAYS days), their headers and the same
    thread rule by which search_messages grouped the messages."""
    typ, data = conn.uid("SEARCH", "OR", "HEADER", "Message-ID", quote(thread), "HEADER", "References", quote(thread))
    if typ == "OK":
        return fetch_headers(conn, (data[0] or b"").split())
    if since:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
            raise MailError("since is a date YYYY-MM-DD")
    else:
        since = (datetime.date.today() - datetime.timedelta(days=THREAD_DAYS)).isoformat()
    typ, data = conn.uid("SEARCH", "SINCE", _imap_date(since))
    _ok(typ, data, "SEARCH")
    uids = (data[0] or b"").split()
    uids.sort(key=int, reverse=True)
    out = []
    for i in range(0, min(len(uids), THREAD_SCAN), THREAD_CHUNK):
        out += [it for it in fetch_headers(conn, uids[i:i + THREAD_CHUNK]) if thread in (it["threadId"], it["messageId"])]
    return out


def read_thread(conn, thread: str, max_chars: int = 40000, since: str = "") -> dict:
    """Messages of a thread from all folders, by date; messages stay unread.
    since (YYYY-MM-DD) is not before the first message of the thread: it narrows the walk over
    servers without a search by headers."""
    thread = thread.strip()
    if not _MSGID.fullmatch(thread):
        raise MailError("thread_id is the Message-ID of the first message of the thread, like <abc@example.com>")
    found: dict[str, tuple[str, int, dict]] = {}
    for folder in thread_folders(conn):
        _examine(conn, folder)
        for item in _thread_items(conn, thread, (since or "").strip()):
            key = item["messageId"] or f"{folder}:{item['uid']}"
            found.setdefault(key, (folder, item["uid"], item))
    if not found:
        raise MailError(f"there is no thread {thread}")
    per = max(2000, int(max_chars or 40000) // len(found))
    messages = []
    for folder, uid, _ in sorted(found.values(), key=lambda v: v[2].get("date") or ""):
        messages.append(read_message(conn, folder, uid, per))
    return {"threadId": thread, "messages": messages}


def read_message(conn, folder: str, uid: int, max_chars: int = 20000) -> dict:
    """Headers, text and a list of attachments; the message stays unread."""
    _examine(conn, folder)
    head = fetch_headers(conn, [str(int(uid)).encode()])
    if not head:
        raise MailError(f"there is no message {uid} in {folder!r}")
    item = head[0]
    item["folder"] = folder
    if item.get("size") and item["size"] > MAX_MESSAGE_BYTES:
        item.update(text="", truncated=True, note=f"the message is larger than {MAX_MESSAGE_BYTES >> 20} MB; only headers")
        return item
    typ, data = conn.uid("FETCH", str(int(uid)), "(BODY.PEEK[])")
    _ok(typ, data, "FETCH")
    raw = next((p[1] for p in data if isinstance(p, tuple)), b"")
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    text, attachments = _text_of(msg)
    limit = max(1000, min(int(max_chars or 20000), 200000))
    item["inReplyTo"] = (msg.get("In-Reply-To") or "").strip()
    item["text"] = text[:limit]
    item["truncated"] = len(text) > limit
    item["attachments"] = attachments
    return item


def close(conn) -> None:
    try:
        conn.logout()
    except Exception:
        pass
