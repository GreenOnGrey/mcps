"""render_document: Markdown с параметрами → DOCX, PPTX и PDF
(FTR.NAB.CHAT-0003 R1, R4, tech §2.3)."""

from __future__ import annotations

import os
import re
import subprocess

import yaml
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm

from . import gotenberg, limits
from .errors import DocError

THEME = os.path.join(os.path.dirname(__file__), "theme")
REFERENCE_DIR = os.environ.get("DOCGEN_REFERENCE_DIR", "/app/theme")
PAGEBREAK = "<!-- pagebreak -->"
OPTIONS = {"title", "author", "lang", "theme", "page", "header", "footer", "toc"}
PAGE_OPTIONS = {"size", "orientation", "margins"}
SIZES = {"A4": (210, 297), "A5": (148, 210), "LETTER": (215.9, 279.4)}
FROM = "markdown+pipe_tables+footnotes+fenced_divs+raw_html+raw_attribute+implicit_figures"


# Подстановка оболочки вместо текста: агент написал $(cat file.md) в аргумент
# инструмента, а не в команду bash, — документ вышел бы из одной этой строки
# (FTR.NAB.CHAT-0003 tech, решение 31). Весь текст — одно выражение: Markdown,
# который лишь начинается с `кода` или $формулы$, проходит.
SHELL_EXPR = re.compile(r"\$\([^\n]*\)|`[^`\n]+`|\$\{?[A-Za-z_]\w*\}?")


def inline(markdown: str) -> str:
    """Markdown из аргумента: не больше MAX_INLINE (R12) и не выражение оболочки."""
    limits.check_inline("markdown", markdown)
    if SHELL_EXPR.fullmatch(markdown.strip()):
        raise DocError("markdown is a shell expression, not the text: the tool does not run the shell. "
                       "Pass the path of the file in source instead.")
    return markdown


def read_source(path: str) -> str:
    """Markdown из файла пространства; размер уже проверил fetch (MAX_SOURCE)."""
    with open(path, "rb") as f:
        data = f.read()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocError("The source file is not UTF-8 text.") from None


def split_options(markdown: str) -> tuple[dict, str]:
    """Блок параметров YAML в начале Markdown (--- … ---) и текст после него."""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", markdown, re.S)
    if not m:
        return {}, markdown
    try:
        opts = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise DocError(f"The options block is not valid YAML: {e}") from None
    if not isinstance(opts, dict):
        raise DocError("The options block must be a mapping.")
    unknown = set(opts) - OPTIONS
    if unknown:
        raise DocError(f"Unknown option {sorted(unknown)[0]}; known: {', '.join(sorted(OPTIONS))}.")
    page = opts.get("page") or {}
    if not isinstance(page, dict) or set(page) - PAGE_OPTIONS:
        raise DocError("page takes size (A4, A5, Letter), orientation (portrait, landscape) and margins (mm).")
    size = str(page.get("size", "A4")).upper()
    if size not in SIZES:
        raise DocError("page.size is A4, A5 or Letter.")
    if page.get("orientation", "portrait") not in ("portrait", "landscape"):
        raise DocError("page.orientation is portrait or landscape.")
    return opts, markdown[m.end():]


def page_mm(opts: dict) -> tuple[float, float, dict]:
    page = opts.get("page") or {}
    w, h = SIZES[str(page.get("size", "A4")).upper()]
    if page.get("orientation") == "landscape":
        w, h = h, w
    margins = {"top": 20, "right": 15, "bottom": 20, "left": 30}  # ГОСТ Р 7.0.97
    m = page.get("margins")
    if isinstance(m, dict):
        for k in margins:
            if k in m:
                margins[k] = float(m[k])
    elif isinstance(m, (int, float)):
        margins = {k: float(m) for k in margins}
    return w, h, margins


def pandoc(workdir: str, md: str, to: str, out: str, opts: dict, extra: list[str]) -> None:
    src = os.path.join(workdir, "document.md")
    with open(src, "w", encoding="utf-8") as f:
        f.write(md)
    args = [limits.PANDOC, src, "-f", FROM, "-t", to, "-o", out, "--resource-path", workdir]
    for key in ("title", "author"):
        if opts.get(key):
            args += ["--metadata", f"{key}={opts[key]}"]
    args += ["--metadata", f"lang={opts.get('lang', 'ru')}"]
    if opts.get("toc"):
        args.append("--toc")
    try:
        r = subprocess.run(args + extra, cwd=workdir, capture_output=True, text=True, timeout=limits.TIMEOUT,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": workdir})
    except subprocess.TimeoutExpired:
        raise DocError(f"The document took longer than {limits.TIMEOUT} s.") from None
    if r.returncode != 0:
        raise DocError("The Markdown could not be converted: " + r.stderr.strip()[:300])


def reference(kind: str, given: str | None) -> str:
    if given:
        if not given.lower().endswith("." + kind):
            raise DocError(f"reference must be a .{kind} file for {kind}.")
        return given
    return os.path.join(REFERENCE_DIR, f"reference.{kind}")


def render(workdir: str, markdown: str, fmt: str, reference_path: str | None) -> tuple[str, list[str]]:
    """Собирает документ; возвращает путь к файлу и предупреждения."""
    opts, md = split_options(markdown)
    warnings: list[str] = []
    out = os.path.join(workdir, "result." + fmt)
    if fmt == "docx":
        md = md.replace(PAGEBREAK, '\n```{=openxml}\n<w:p><w:r><w:br w:type="page"/></w:r></w:p>\n```\n')
        pandoc(workdir, md, "docx", out, opts, ["--reference-doc", reference("docx", reference_path)])
        layout_docx(out, opts, own=reference_path is None)
    elif fmt == "pptx":
        md = md.replace(PAGEBREAK, "")
        pandoc(workdir, md, "pptx", out, opts, ["--slide-level=2", "--reference-doc", reference("pptx", reference_path)])
    elif fmt == "pdf":
        if reference_path:
            warnings.append("reference applies to docx and pptx only")
        md = md.replace(PAGEBREAK, '\n<div style="break-after: page"></div>\n')
        html = os.path.join(workdir, "index.html")
        pandoc(workdir, md, "html5", html, opts, ["--standalone", "--embed-resources", "--template",
                                                  os.path.join(THEME, "pdf.html"), "--data-dir", THEME])
        with open(html, "rb") as f:
            index = f.read()
        w, h, m = page_mm(opts)
        inch = 25.4
        options = {"paperWidth": round(w / inch, 3), "paperHeight": round(h / inch, 3),
                   "marginTop": round(m["top"] / inch, 3), "marginBottom": round(m["bottom"] / inch, 3),
                   "marginLeft": round(m["left"] / inch, 3), "marginRight": round(m["right"] / inch, 3),
                   "printBackground": True, "skipNetworkIdleEvent": True}
        assets = []
        header, footer = opts.get("header"), opts.get("footer", "{page}")
        if header:
            assets.append(("header.html", running(str(header))))
        if footer:
            assets.append(("footer.html", running(str(footer))))
        with open(out, "wb") as f:
            f.write(gotenberg.html_to_pdf(index, assets, options))
    else:
        raise DocError("format is docx, pdf or pptx.")
    return out, warnings


def running(text: str) -> bytes:
    """Колонтитул Chromium: {page} и {pages} — номер страницы и число страниц."""
    import html
    body = html.escape(text).replace("{page}", '<span class="pageNumber"></span>').replace("{pages}", '<span class="totalPages"></span>')
    return ('<html><head><style>body{font-family:"PT Sans",sans-serif;font-size:8pt;color:#666;'
            'width:100%;text-align:center;margin:0 10mm}</style></head><body>' + body + "</body></html>").encode()


def layout_docx(path: str, opts: dict, own: bool = True) -> None:
    """Размер, ориентация и поля страницы, колонтитулы с номером страницы.

    own — документ на своём образце: страница по умолчанию (A4, поля ГОСТ) и
    номер страницы внизу. На образце пользователя (R4) его страница и
    колонтитулы остаются, меняется только то, что задано в параметрах явно.
    """
    w, h, m = page_mm(opts)
    page = opts.get("page") or {}
    doc = Document(path)
    for s in doc.sections:
        if own or "size" in page or "orientation" in page:
            s.page_width, s.page_height = Mm(w), Mm(h)
            s.orientation = WD_ORIENT.LANDSCAPE if w > h else WD_ORIENT.PORTRAIT
        if own or page.get("margins") is not None:
            s.top_margin, s.bottom_margin = Mm(m["top"]), Mm(m["bottom"])
            s.left_margin, s.right_margin = Mm(m["left"]), Mm(m["right"])
        if opts.get("header"):
            field_text(s.header.paragraphs[0], str(opts["header"]))
        if own or "footer" in opts:
            field_text(s.footer.paragraphs[0], str(opts.get("footer", "{page}")))
    doc.save(path)


def field_text(paragraph, text: str) -> None:
    """Текст абзаца, где {page} и {pages} — поля PAGE и NUMPAGES."""
    paragraph.alignment = 1
    for part in re.split(r"(\{page\}|\{pages\})", text):
        if part in ("{page}", "{pages}"):
            fld = OxmlElement("w:fldSimple")
            fld.set(qn("w:instr"), "PAGE" if part == "{page}" else "NUMPAGES")
            r = OxmlElement("w:r")
            t = OxmlElement("w:t")
            t.text = "1"
            r.append(t)
            fld.append(r)
            paragraph._p.append(fld)
        elif part:
            paragraph.add_run(part)
