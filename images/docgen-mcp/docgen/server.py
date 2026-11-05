"""docgen: the MCP server of documents (FTR.NAB.CHAT-0003 R1–R9, R12, R19–R21).

The tools create, fill, edit and convert documents. Files
come as links of the Nabu proxy (with the path in the workspace in the fragment), and the result
goes with PUT by the link dest; the proxy moves it to the workspace of the agent.
Each call works in a temporary directory of its own, deleted after
the call; contents of documents are not logged.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import shutil
import tempfile
import time

import anyio
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pypdf import PdfReader

from . import converting, documents, editing, fetch, limits, pdfs, sheets, templates
from .errors import DocError

log = logging.getLogger("docgen")
# The tools write only the result file and do not go to the outside world (R25).
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

OPERATIONS = Counter("docgen_operations_total", "Operations of docgen by tool and result.", ["tool", "result"])
SECONDS = Histogram("docgen_operation_seconds", "Duration of operations of docgen.", ["tool", "format"],
                    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120))

mcp = MCPServer(
    "docgen",
    instructions=(
        "Documents of the user's Nabu space: create DOCX, PDF and PPTX from Markdown (render_document), XLSX from a "
        "description (render_spreadsheet), fill templates (fill_template), edit (edit_document), convert office files "
        "and HTML to PDF (convert), merge or split PDF (pdf_tools). File arguments and dest are paths in the space."
    ),
)


def _run(tool: str, fmt: str, fn):
    """Does the work in a directory of its own with a time limit; errors go to the agent."""

    def work():
        os.makedirs(limits.WORK_DIR, exist_ok=True)
        workdir = tempfile.mkdtemp(prefix="call-", dir=limits.WORK_DIR)
        try:
            return fn(workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    async def runner():
        start = time.monotonic()
        try:
            with anyio.fail_after(limits.TIMEOUT):
                result = await anyio.to_thread.run_sync(work, abandon_on_cancel=True)
        except TimeoutError:
            OPERATIONS.labels(tool, "timeout").inc()
            raise ToolError(f"The operation took longer than {limits.TIMEOUT} s.") from None
        except DocError as e:
            OPERATIONS.labels(tool, "error").inc()
            log.info("docgen: %s %s: refused", tool, fmt)
            raise ToolError(str(e)) from None
        except ToolError:
            OPERATIONS.labels(tool, "error").inc()
            raise
        except Exception as e:  # an error parsing a file and so on: the reason without contents
            OPERATIONS.labels(tool, "error").inc()
            log.warning("docgen: %s %s: %s", tool, fmt, type(e).__name__)
            raise ToolError(f"The document could not be processed: {type(e).__name__}: {str(e)[:200]}") from None
        took = time.monotonic() - start
        OPERATIONS.labels(tool, "ok").inc()
        SECONDS.labels(tool, fmt).observe(took)
        log.info("docgen: %s %s: %d bytes in %.1f s", tool, fmt, result.get("bytes", 0), took)
        return json.dumps(result, ensure_ascii=False)

    return runner()


def _finish(path: str, dest: str, fmt: str, warnings=None, extra=None) -> dict:
    """Checks the pages of a PDF and uploads the result."""
    info = {"format": fmt}
    if fmt == "pdf":
        pages = len(PdfReader(path).pages)
        if pages > limits.MAX_PAGES:
            raise DocError(f"The result has {pages} pages, more than {limits.MAX_PAGES}.")
        info["pages"] = pages
    info["bytes"] = fetch.upload(dest, path)
    if extra:
        info.update(extra)
    if warnings:
        info["warnings"] = [f"Not loaded: {w}" if "://" in w or w.startswith("//") else w for w in warnings]
    return info


def _get(url: str, workdir: str) -> str:
    if not url:
        raise DocError("A file argument is empty.")
    return fetch.download(url, workdir)


def _markdown(source: str, markdown: str, workdir: str) -> str:
    """The text of a document: a workspace file (source) or the text itself (markdown), exactly one of them
    (FTR.NAB.CHAT-0003 tech, decision 31)."""
    if not source and not markdown.strip():
        raise DocError("Give the Markdown in source (a .md file of the space) or in markdown (the text).")
    if source and markdown.strip():
        raise DocError("Give the Markdown in source or in markdown, not both.")
    if source:
        if "://" not in source:
            # the path did not become a link: the catalog item has no file argument render_document.source
            raise DocError("source is not enabled for this server in the catalog of Nabu: pass the text in markdown, "
                           "or ask the administrator to add render_document.source to the file arguments.")
        return documents.read_source(_get(source, workdir))
    return documents.inline(markdown)


# ─── Tools ─────────────────────────────────────────────────────────
# The docstring of a tool is its description in tools/list for the agent, like
# the texts of errors (tech §2.2).

@mcp.tool(annotations=WRITE)
async def render_document(format: str, dest: str, source: str = "", markdown: str = "", reference: str = "",
                          images: list[str] = [],  # noqa: B006 — the schema of a list of files (R11)
                          overwrite: bool = False) -> str:
    """Creates a DOCX, PDF or PPTX document from Markdown.

    source: a .md file of the space — use it for any document saved in the space; markdown: the text itself,
    for a short document written right in the call (never a shell expression like $(cat file)). Give one of them.
    The Markdown may start with an options block (--- yaml ---): title, author, lang, page (size A4/A5/Letter,
    orientation, margins in mm), header and footer ({page}, {pages}), toc. Tables, footnotes, images (paths of
    the space listed in images), <!-- pagebreak -->. In PPTX each ## heading starts a slide, ::: notes are
    speaker notes. reference: a .docx or .pptx of the space whose styles to use. dest: where to save the file.
    """
    def work(workdir):
        for u in images or []:
            _get(u, workdir)
        ref = _get(reference, workdir) if reference else None
        path, warnings = documents.render(workdir, _markdown(source, markdown, workdir), format, ref)
        return _finish(path, dest, format, warnings)

    return await _run("render_document", format, work)


@mcp.tool(annotations=WRITE)
async def render_spreadsheet(workbook: dict, dest: str, template: str = "", overwrite: bool = False) -> str:
    """Creates an XLSX workbook: workbook.sheets[] with name, columns (header, width, type: text, number,
    integer, date, datetime, currency, percent, bool; format), rows (a value starting with = is a formula),
    freeze (e.g. A2), autofilter, merge ranges and charts (type bar/line/pie, categories, values, anchor).
    Formulas are kept: Excel computes them on open. template: an .xlsx of the space to write into."""
    def work(workdir):
        tpl = _get(template, workdir) if template else None
        out = os.path.join(workdir, "result.xlsx")
        n = sheets.render(workbook, out, tpl)
        return _finish(out, dest, "xlsx", extra={"sheets": n})

    return await _run("render_spreadsheet", "xlsx", work)


@mcp.tool(annotations=WRITE)
async def fill_template(template: str, data: dict, dest: str, overwrite: bool = False) -> str:
    """Fills a .docx, .xlsx or .pptx template of the space: fields {{ name }}, loops over table rows
    {%tr for item in items %} … {%tr endfor %} (DOCX, XLSX). A field missing from data is an error."""
    def work(workdir):
        tpl = _get(template, workdir)
        ext = converting.ext_of(tpl)
        out = os.path.join(workdir, "result." + ext)
        templates.fill(tpl, data, out)
        return _finish(out, dest, ext)

    return await _run("fill_template", "template", work)


@mcp.tool(annotations=WRITE)
async def edit_document(source: str, ops: list[dict], dest: str, overwrite: bool = False) -> str:
    """Edits a file of the space. DOCX and PPTX: {op: replace_text, find, replace, all} keeps the formatting.
    XLSX: {op: set_cells, sheet, range, values}, {op: append_rows, sheet, rows}, {op: add_sheet, name}.
    dest may be the source itself with overwrite: true."""
    def work(workdir):
        src = _get(source, workdir)
        ext = converting.ext_of(src)
        out = os.path.join(workdir, "result." + ext)
        done = editing.edit(src, ops, out)
        return _finish(out, dest, ext, extra=done)

    return await _run("edit_document", "edit", work)


@mcp.tool(annotations=WRITE)
async def convert(source: str, to: str, dest: str, options: dict | None = None, overwrite: bool = False) -> str:
    """Converts a file of the space: office documents (DOCX, DOC, ODT, RTF, XLSX, XLS, ODS, PPTX, PPT, ODP)
    to PDF; DOC, XLS, PPT to DOCX, XLSX, PPTX; an .html page or a .zip with index.html to PDF. HTML runs its
    JavaScript without network: embed styles, images and scripts, take libraries from /opt/nabu/assets of the
    sandbox and set window.status = 'ready' when charts are drawn. options: paper, landscape, margins (mm),
    printBackground, waitForReady."""
    def work(workdir):
        src = _get(source, workdir)
        data, warnings = converting.convert(src, to, options or {}, workdir)
        out = os.path.join(workdir, "result." + to)
        with open(out, "wb") as f:
            f.write(data)
        return _finish(out, dest, to, warnings)

    return await _run("convert", to, work)


@mcp.tool(annotations=WRITE)
async def pdf_tools(op: str, sources: list[str], dest: str, pages: str = "", angle: int = 90,
                    overwrite: bool = False) -> str:
    """PDF of the space: merge (sources in order), split (pages like 1-3,5; the result is a .zip of parts),
    rotate (pages, angle 90/180/270) or extract (pages into a new PDF)."""
    def work(workdir):
        files = [_get(u, workdir) for u in sources]
        data, ext, n = pdfs.run(op, files, pages, angle)
        out = os.path.join(workdir, "result." + ext)
        with open(out, "wb") as f:
            f.write(data)
        return _finish(out, dest, ext, extra={"pages": n})

    return await _run("pdf_tools", op, work)


# ─── HTTP ───────────────────────────────────────────────────────────

def _plain(status: int, body: bytes, content_type: bytes = b"application/json"):
    async def send_response(send):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", content_type), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
    return send_response


async def _body(receive, limit: int = 1 << 20) -> bytes:
    out = b""
    while True:
        msg = await receive()
        out += msg.get("body", b"")
        if len(out) > limit:
            raise DocError("The request is too large.")
        if not msg.get("more_body"):
            return out


async def xlsx_preview(receive, send):
    """The sheets of a workbook for the preview of the site (FTR.NAB.CHAT-0003 R14, tech §2.5)."""
    try:
        req = json.loads(await _body(receive))
        url = str(req["url"])

        def work():
            os.makedirs(limits.WORK_DIR, exist_ok=True)
            workdir = tempfile.mkdtemp(prefix="preview-", dir=limits.WORK_DIR)
            try:
                return sheets.preview(fetch.download(url, workdir), int(req.get("maxRows", 500)), int(req.get("maxCols", 50)))
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

        with anyio.fail_after(limits.TIMEOUT):
            data = await anyio.to_thread.run_sync(work, abandon_on_cancel=True)
    except (KeyError, ValueError, DocError) as e:
        return await _plain(422, json.dumps({"error": "invalid", "message": str(e)[:200]}).encode())(send)
    except TimeoutError:
        return await _plain(504, b'{"error":"timeout"}')(send)
    except Exception as e:
        log.warning("docgen: preview: %s", type(e).__name__)
        return await _plain(422, json.dumps({"error": "broken", "message": type(e).__name__}).encode())(send)
    return await _plain(200, json.dumps(data, ensure_ascii=False).encode())(send)


def with_auth(app):
    """The key on everything except /healthz and /metrics; the internal preview of tables."""
    key = limits.KEY.encode()

    async def wrapper(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        path = scope["path"]
        if path == "/healthz":
            return await _plain(200, b"ok\n", b"text/plain")(send)
        if path == "/metrics":
            return await _plain(200, generate_latest(), CONTENT_TYPE_LATEST.encode())(send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if not key or scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), key):
            return await _plain(401, b'{"error":"unauthorized"}')(send)
        if path == "/internal/xlsx-preview" and scope["method"] == "POST":
            return await xlsx_preview(receive, send)
        return await app(scope, receive, send)

    return wrapper


def build_app():
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0",
                                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    return with_auth(app)


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="warning", access_log=False)
