"""imap-mcp — MCP-сервер почтового ящика по IMAP только для чтения (FTR.NAB.CMN-0006 R20–R22).

Прокси MCP Nabu добавляет OAuth-токен пользователя в каждый запрос; сервер
узнаёт по токену адрес ящика, входит по XOAUTH2 и читает, ничего не меняя.
Streamable HTTP без состояния: между запросами в памяти остаётся только
адрес ящика по хешу токена.
"""

from __future__ import annotations

import imaplib
import json
import logging
import os

import anyio
import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

import imap_core as core

log = logging.getLogger("imap-mcp")
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

mcp = MCPServer(
    "imap-mcp",
    instructions=(
        "Read-only access to the user's mailbox. Find messages with search_messages, then read one with "
        "read_message by its folder and uid. Nothing can be sent, moved, deleted or marked as read."
    ),
)


AUTH_FAILED = "imap_mcp_auth_failed"


def _reason(e: Exception) -> str:
    """Причина ошибки протокола без данных ящика: imaplib дописывает к «unexpected
    response» строку ответа сервера — она отрезается, как и всё после 200 символов."""
    text = str(e)
    for cut in ("unexpected response", "unexpected tagged response", "unexpected untagged response"):
        i = text.find(cut)
        if i >= 0:
            text = text[: i + len(cut)]
            break
    return text[:200]


def _token(headers) -> str:
    value = (headers or {}).get("authorization") or (headers or {}).get("Authorization") or ""
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() in ("bearer", "oauth") else ""


async def _run(ctx: Context, fn):
    """Входит в ящик с токеном запроса и выполняет fn(conn) в отдельном потоке."""
    token = _token(ctx.headers)

    def once(address):
        conn = core.connect(address, token)
        try:
            return fn(conn)
        finally:
            core.close(conn)

    def work():
        address = core.resolve_email(token)
        try:
            return once(address)
        except (imaplib.IMAP4.abort, OSError) as e:
            # сервер оборвал соединение (Яндекс так делает время от времени):
            # всё только читается — повторяем один раз на новом соединении
            log.warning("imap-mcp: %s: %s; retrying", type(e).__name__, _reason(e))
            return once(address)

    try:
        result = await anyio.to_thread.run_sync(work)
    except core.AuthError:
        core.forget(token)
        # R21: отказ IMAP — тоже 401: обёртка with_auth подменит ответ, и Nabu
        # покажет пользователю «подключите заново»
        request = getattr(ctx.request_context, "request", None)
        if request is not None:
            request.scope.setdefault("state", {})[AUTH_FAILED] = True
        raise ToolError("The access to the mailbox was refused: connect the mail again in Connections.") from None
    except core.MailError as e:
        raise ToolError(str(e)) from None
    except Exception as e:  # агенту и в журнал — причина без содержимого ящика (_reason)
        log.warning("imap-mcp: %s: %s", type(e).__name__, _reason(e))
        raise ToolError(f"The mail server failed: {type(e).__name__}: {_reason(e)}") from None
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(annotations=READ)
async def list_folders(ctx: Context) -> str:
    """Folders of the mailbox: name (as shown to the user) and special role (Inbox, Sent, Drafts, Trash, Spam)."""
    return await _run(ctx, core.list_folders)


@mcp.tool(annotations=READ)
async def search_messages(
    ctx: Context,
    folder: str = "INBOX",
    sender: str = "",
    recipient: str = "",
    subject: str = "",
    text: str = "",
    since: str = "",
    before: str = "",
    unseen: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> str:
    """Finds messages in a folder, newest first. All conditions are optional and combined with AND.

    sender, recipient, subject, text: substrings; since and before: dates YYYY-MM-DD (before is exclusive);
    unseen: only unread messages; limit: at most 50; offset: skip that many newest messages (paging).
    Returns uid, from, to, cc, subject, date, threadId, whether the message is read and whether it is a
    mailing (bulk); read the text with read_message or the whole thread with read_thread.
    """
    q = core.Query(folder=folder or "INBOX", sender=sender, recipient=recipient, subject=subject, text=text,
                   since=since, before=before, unseen=unseen, limit=limit, offset=offset)
    return await _run(ctx, lambda conn: core.search(conn, q))


@mcp.tool(annotations=READ)
async def read_message(ctx: Context, folder: str, uid: int, max_chars: int = 20000) -> str:
    """Reads one message by folder and uid: headers, text (HTML without markup) and the list of attachments
    (names and sizes, without content). The message stays unread."""
    return await _run(ctx, lambda conn: core.read_message(conn, folder, uid, max_chars))


@mcp.tool(annotations=READ)
async def read_thread(ctx: Context, thread_id: str, max_chars: int = 40000, since: str = "") -> str:
    """Reads a whole thread from every folder of the mailbox, oldest first: headers and text of each
    message. thread_id is the threadId of search_messages (the Message-ID of the first message);
    since (YYYY-MM-DD, optional) is a day not later than the first message — it speeds up servers
    that cannot search by headers. The messages stay unread."""
    return await _run(ctx, lambda conn: core.read_thread(conn, thread_id, max_chars, since))


def _plain(status: int, body: bytes, content_type: bytes = b"application/json"):
    async def send_response(send):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", content_type), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
    return send_response


def with_auth(app):
    """401 без токена, с токеном, который отверг сервис адреса, и при отказе IMAP
    во время вызова (R21); /healthz — без токена."""

    async def wrapper(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        if scope["path"] == "/healthz":
            return await _plain(200, b"ok\n", b"text/plain")(send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        token = _token(headers)
        unauthorized = _plain(401, b'{"error":"unauthorized","message":"a Bearer token of the mailbox owner is required"}')
        if not token:
            return await unauthorized(send)
        try:
            await anyio.to_thread.run_sync(core.resolve_email, token)
        except core.AuthError:
            return await unauthorized(send)
        except core.MailError as e:
            log.warning("imap-mcp: account service: %s", e)
            return await _plain(502, json.dumps({"error": "upstream", "message": str(e)}).encode())(send)
        state = scope.setdefault("state", {})

        async def guarded_send(message):
            # ответ JSON отправляется после выполнения инструмента: если IMAP
            # отверг токен, вместо ответа — 401
            if state.get(AUTH_FAILED):
                if message["type"] == "http.response.start":
                    await unauthorized(send)
                return
            await send(message)

        return await app(scope, receive, guarded_send)

    return wrapper


def build_app():
    # Защита от DNS rebinding нужна локальным серверам; этот сервер вызывают по
    # имени сервиса внутри кластера, и каждый вызов требует токен.
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0",
                                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return with_auth(app)


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="warning",
                access_log=False)
