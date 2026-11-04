"""pdf_tools: склеить, разрезать, повернуть, извлечь страницы (FTR.NAB.CHAT-0003 R9)."""

from __future__ import annotations

import io
import zipfile

from pypdf import PdfReader, PdfWriter

from . import limits
from .errors import DocError


def ranges(spec: str, pages: int) -> list[list[int]]:
    """«1-3,5» → [[0,1,2],[4]] (номера с единицы)."""
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        try:
            lo, hi = int(a), int(b or a)
        except ValueError:
            raise DocError(f"pages {spec!r}: use numbers and ranges like 1-3,5.") from None
        if lo < 1 or hi > pages or lo > hi:
            raise DocError(f"pages {part} is outside 1-{pages}.")
        out.append(list(range(lo - 1, hi)))
    if not out:
        raise DocError("pages is required, e.g. 1-3,5.")
    return out


def run(op: str, sources: list[str], pages: str, angle: int) -> tuple[bytes, str, int]:
    """Возвращает содержимое, расширение результата (pdf или zip) и число страниц."""
    if not sources:
        raise DocError("sources is a non-empty list.")
    readers = [PdfReader(s) for s in sources]
    writer = PdfWriter()
    if op == "merge":
        for r in readers:
            for p in r.pages:
                writer.add_page(p)
    elif op in ("extract", "rotate", "split"):
        r = readers[0]
        groups = ranges(pages, len(r.pages)) if (pages or op != "rotate") else [list(range(len(r.pages)))]
        if op == "split":
            buf = io.BytesIO()
            total = 0
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for n, g in enumerate(groups, start=1):
                    w = PdfWriter()
                    for i in g:
                        w.add_page(r.pages[i])
                    part = io.BytesIO()
                    w.write(part)
                    zf.writestr(f"part-{n}.pdf", part.getvalue())
                    total += len(g)
            return buf.getvalue(), "zip", total
        if op == "extract":
            for g in groups:
                for i in g:
                    writer.add_page(r.pages[i])
        else:
            if angle not in (90, 180, 270):
                raise DocError("angle is 90, 180 or 270.")
            chosen = {i for g in groups for i in g}
            for i, p in enumerate(r.pages):
                if i in chosen:
                    p.rotate(angle)
                writer.add_page(p)
    else:
        raise DocError("op is merge, split, rotate or extract.")
    if len(writer.pages) > limits.MAX_PAGES:
        raise DocError(f"The result has more than {limits.MAX_PAGES} pages.")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), "pdf", len(writer.pages)
