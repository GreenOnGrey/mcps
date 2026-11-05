"""convert: office formats, HTML and ZIP → PDF; DOC/XLS/PPT → OOXML
(FTR.NAB.CHAT-0003 R7, R8, tech §2.3, §2.4)."""

from __future__ import annotations

import os
import posixpath
import stat
import zipfile

from . import gotenberg, htmlscan, limits
from .errors import DocError

OFFICE = {"docx", "doc", "docm", "odt", "rtf", "xlsx", "xls", "xlsm", "ods", "pptx", "ppt", "pptm", "odp"}
LEGACY = {("doc", "docx"), ("xls", "xlsx"), ("ppt", "pptx"), ("odt", "docx"), ("ods", "xlsx"), ("odp", "pptx"), ("rtf", "docx")}
PAPER = {"A4": (8.27, 11.69), "A5": (5.83, 8.27), "LETTER": (8.5, 11)}
# files of a site in which links to its other files are rewritten (R8)
LINKING = {"html", "htm", "css", "js", "mjs", "svg"}


def ext_of(path: str) -> str:
    return path.lower().rsplit(".", 1)[-1] if "." in os.path.basename(path) else ""


def convert(source: str, to: str, options: dict, workdir: str) -> tuple[bytes, list[str]]:
    ext = ext_of(source)
    options = options or {}
    if to == "pdf" and ext in OFFICE:
        return gotenberg.office_to_pdf(source), []
    if (ext, to) in LEGACY:
        return gotenberg.legacy(source, to), []
    if to == "pdf" and ext in ("html", "htm"):
        with open(source, "rb") as f:
            index = f.read()
        return gotenberg.html_to_pdf(index, [], chromium(options, index)), htmlscan.external(index)
    if to == "pdf" and ext == "zip":
        index, assets = unpack(source, os.path.join(workdir, "site"))
        return gotenberg.html_to_pdf(index, assets, chromium(options, index + b"".join(d for n, d in assets if ext_of(n) in ("js", "mjs")))), \
            htmlscan.external(index)
    raise DocError(f"A .{ext or '?'} file cannot be converted to {to}.")


def chromium(options: dict, page: bytes = b"") -> dict:
    """Parameters of Chromium: the format from CSS @page unless the agent set its own.

    Gotenberg waits for waitForExpression with no way out, until its timeout.
    So we wait for window.status = 'ready' only if the page (or its scripts)
    sets it; otherwise a short pause for scripts to render.
    """
    out = {"printBackground": bool(options.get("printBackground", True)), "skipNetworkIdleEvent": True}
    if options.get("waitForReady", b"window.status" in page):
        out["waitForExpression"] = "window.status === 'ready'"
    else:
        out["waitDelay"] = "1s"
    if options.get("paper") or "landscape" in options:
        w, h = PAPER.get(str(options.get("paper", "A4")).upper(), PAPER["A4"])
        out.update({"paperWidth": w, "paperHeight": h, "landscape": bool(options.get("landscape", False))})
    else:
        out["preferCssPageSize"] = True
    margins = options.get("margins")
    if isinstance(margins, dict):
        for k in ("top", "right", "bottom", "left"):
            if k in margins:
                out["margin" + k.capitalize()] = round(float(margins[k]) / 25.4, 3)
    return out


def unpack(path: str, dest: str) -> tuple[bytes, list[tuple[str, bytes]]]:
    """Unpacks a site from an archive with checks (tech §2.4); Gotenberg
    takes files as one directory, so resources go by their names."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise DocError("The archive is damaged.") from None
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > limits.ZIP_MAX_FILES:
            raise DocError(f"The archive has more than {limits.ZIP_MAX_FILES} files.")
        if sum(i.file_size for i in infos) > limits.ZIP_MAX:
            raise DocError(f"The archive unpacks to more than {limits.ZIP_MAX // limits.MB} MB.")
        for i in infos:
            name = i.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise DocError(f"The archive has an unsafe path: {name}")
            if stat.S_ISLNK(i.external_attr >> 16):
                raise DocError(f"The archive has a link: {name}")
            if i.compress_size and i.file_size / i.compress_size > 200:
                raise DocError(f"The archive is suspiciously compressed: {name}")
        names = [i.filename for i in infos]
        roots = {n.split("/")[0] for n in names if "/" in n}
        prefix = ""
        if "index.html" not in names and len(roots) == 1 and f"{next(iter(roots))}/index.html" in names:
            prefix = next(iter(roots)) + "/"
        if prefix + "index.html" not in names:
            raise DocError("The archive has no index.html at its root.")
        total = 0
        index, files, seen, moved = b"", [], set(), []
        for i in infos:
            data = zf.read(i)
            total += len(data)
            if total > limits.ZIP_MAX:
                raise DocError(f"The archive unpacks to more than {limits.ZIP_MAX // limits.MB} MB.")
            rel = i.filename[len(prefix):] if i.filename.startswith(prefix) else i.filename
            if rel == "index.html":
                index = data
                continue
            base = os.path.basename(rel)
            if base in seen:
                raise DocError(f"Two files of the archive have the name {base}; Gotenberg takes one folder of files.")
            seen.add(base)
            files.append((rel, base, data))
            if rel != base:
                moved.append((rel, base))
    index = relink(index, "", moved)
    assets = [(base, relink(data, posixpath.dirname(rel), moved) if ext_of(rel) in LINKING else data) for rel, base, data in files]
    return index, assets


def relink(data: bytes, folder: str, moved: list[tuple[str, str]]) -> bytes:
    """Links of a site file from the folder to moved files: img/logo.png
    in index.html and ../img/logo.png in css/style.css become logo.png, as
    the files of a request to Gotenberg lie in one folder."""
    for rel, base in sorted(moved, key=lambda m: -len(m[0])):
        link = posixpath.relpath(rel, folder or ".").encode()
        for q in (b'"', b"'", b"("):
            data = data.replace(q + link, q + base.encode()).replace(q + b"./" + link, q + base.encode())
    return data
