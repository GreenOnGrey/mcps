"""edit_document: typical edits of DOCX, PPTX and XLSX (FTR.NAB.CHAT-0003 R5)."""

from __future__ import annotations

from docx import Document
from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries
from pptx import Presentation

from .errors import DocError


def replace_in_runs(runs, find: str, replace: str, all_: bool) -> int:
    """Replaces text of a paragraph split into formatting runs: the replacement
    gets the formatting of the run where the match started, the affected
    next runs get shorter, and others do not change."""
    if not find:
        return 0
    count, search_from = 0, 0
    while True:
        texts = [r.text for r in runs]
        full = "".join(texts)
        start = full.find(find, search_from)
        if start < 0:
            return count
        end = start + len(find)
        offsets, pos = [], 0
        for t in texts:
            offsets.append(pos)
            pos += len(t)
        first = next(i for i, t in enumerate(texts) if offsets[i] <= start < offsets[i] + len(t))
        last = first
        while offsets[last] + len(texts[last]) < end:
            last += 1
        head = texts[first][: start - offsets[first]]
        tail = texts[last][end - offsets[last]:]
        if last == first:
            runs[first].text = head + replace + tail
        else:
            runs[first].text = head + replace
            for j in range(first + 1, last):
                runs[j].text = ""
            runs[last].text = tail
        count += 1
        if not all_:
            return count
        search_from = start + len(replace)


def _docx_paragraphs(doc):
    yield from doc.paragraphs
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs
    for s in doc.sections:
        for part in (s.header, s.footer):
            yield from part.paragraphs


def _pptx_paragraphs(prs):
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                yield from shape.text_frame.paragraphs
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        yield from cell.text_frame.paragraphs


def edit(source: str, ops: list, out: str) -> dict:
    """Applies operations in order; returns how many replacements and rows were made."""
    if not isinstance(ops, list) or not ops:
        raise DocError("ops is a non-empty list.")
    ext = source.lower().rsplit(".", 1)[-1]
    done = {"replaced": 0, "rows": 0, "cells": 0, "sheets": 0}
    if ext in ("docx", "pptx"):
        for i, op in enumerate(ops):
            if op.get("op") != "replace_text":
                raise DocError(f"ops[{i}]: {op.get('op')!r} does not apply to .{ext}; use replace_text.")
        doc = Document(source) if ext == "docx" else Presentation(source)
        paragraphs = _docx_paragraphs if ext == "docx" else _pptx_paragraphs
        for i, op in enumerate(ops):
            find, repl = str(op.get("find", "")), str(op.get("replace", ""))
            if not find:
                raise DocError(f"ops[{i}].find is required.")
            n = 0
            for p in paragraphs(doc):
                n += replace_in_runs(p.runs, find, repl, bool(op.get("all", True)))
                if n and not op.get("all", True):
                    break
            done["replaced"] += n
        doc.save(out)
        return done
    if ext == "xlsx":
        wb = load_workbook(source)
        for i, op in enumerate(ops):
            kind = op.get("op")
            if kind == "add_sheet":
                name = str(op.get("name", "")).strip()[:31]
                if not name or name in wb.sheetnames:
                    raise DocError(f"ops[{i}].name is required and must be new.")
                wb.create_sheet(name)
                done["sheets"] += 1
                continue
            sheet = op.get("sheet")
            if sheet not in wb.sheetnames:
                raise DocError(f"ops[{i}].sheet {sheet!r} is not in the workbook.")
            ws = wb[sheet]
            if kind == "set_cells":
                c1, r1, c2, r2 = range_boundaries(str(op.get("range", "")))
                values = op.get("values") or []
                if len(values) != r2 - r1 + 1 or any(len(r) != c2 - c1 + 1 for r in values):
                    raise DocError(f"ops[{i}].values must match the range {op.get('range')}.")
                for dr, row in enumerate(values):
                    for dc, v in enumerate(row):
                        ws.cell(row=r1 + dr, column=c1 + dc, value=v)
                        done["cells"] += 1
            elif kind == "append_rows":
                last = 0
                for row in ws.iter_rows():
                    if any(c.value is not None for c in row):
                        last = row[0].row
                for dr, row in enumerate(op.get("rows") or []):
                    for dc, v in enumerate(row):
                        ws.cell(row=last + 1 + dr, column=1 + dc, value=v)
                    done["rows"] += 1
            else:
                raise DocError(f"ops[{i}]: {kind!r} does not apply to .xlsx; use set_cells, append_rows or add_sheet.")
        wb.save(out)
        return done
    raise DocError("source is a .docx, .pptx or .xlsx file.")
