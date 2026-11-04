"""Таблицы XLSX (FTR.NAB.CHAT-0003 R2, R14, tech §2.3, §2.5):
render_spreadsheet по описанию книги и листы книги для предпросмотра сайта."""

from __future__ import annotations

import datetime as dt

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import range_boundaries

from . import limits
from .errors import DocError

TYPES = {"text", "number", "integer", "date", "datetime", "currency", "percent", "bool"}
FORMATS = {"integer": "0", "number": "#,##0.00", "currency": "#,##0.00 ₽", "percent": "0.0%", "date": "DD.MM.YYYY",
           "datetime": "DD.MM.YYYY HH:MM"}
CHARTS = {"bar": BarChart, "line": LineChart, "pie": PieChart}


def _value(v, typ: str, where: str):
    if isinstance(v, str) and v.startswith("'="):
        return v[1:]
    if isinstance(v, str) and v.startswith("="):
        return v  # formula: Excel computes it on open
    if v is None or typ == "text":
        return v
    try:
        if typ in ("number", "currency", "percent"):
            return float(v)
        if typ == "integer":
            return int(v)
        if typ == "date":
            return dt.date.fromisoformat(str(v))
        if typ == "datetime":
            return dt.datetime.fromisoformat(str(v))
        if typ == "bool":
            return bool(v)
    except (TypeError, ValueError):
        raise DocError(f"{where}: {v!r} is not a {typ}.") from None
    return v


def render(workbook: dict, out: str, template: str | None = None) -> int:
    """Пишет книгу; возвращает число листов."""
    limits.check_inline("workbook", workbook)
    if not isinstance(workbook, dict) or not isinstance(workbook.get("sheets"), list) or not workbook["sheets"]:
        raise DocError("workbook.sheets is a non-empty list.")
    wb = load_workbook(template) if template else Workbook()
    if not template:
        wb.remove(wb.active)
    for si, sheet in enumerate(workbook["sheets"]):
        path = f"sheets[{si}]"
        if not isinstance(sheet, dict) or not str(sheet.get("name", "")).strip():
            raise DocError(f"{path}.name is required.")
        name = str(sheet["name"])[:31]
        if name in wb.sheetnames:
            idx = wb.sheetnames.index(name)
            wb.remove(wb[name])
            ws = wb.create_sheet(name, idx)
        else:
            ws = wb.create_sheet(name)
        cols = sheet.get("columns") or []
        types = []
        for ci, c in enumerate(cols):
            typ = c.get("type", "text")
            if typ not in TYPES:
                raise DocError(f"{path}.columns[{ci}].type {typ!r} is unknown; one of {', '.join(sorted(TYPES))}.")
            types.append(typ)
            cell = ws.cell(row=1, column=ci + 1, value=c.get("header", ""))
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="EEEAFF")
            if c.get("width"):
                ws.column_dimensions[get_column_letter(ci + 1)].width = float(c["width"])
        start = 2 if cols else 1
        for ri, row in enumerate(sheet.get("rows") or []):
            if not isinstance(row, list):
                raise DocError(f"{path}.rows[{ri}] is a list of cells.")
            if cols and len(row) > len(cols):
                raise DocError(f"{path}.rows[{ri}] has {len(row)} cells, the sheet has {len(cols)} columns.")
            for ci, v in enumerate(row):
                typ = types[ci] if ci < len(types) else "text"
                cell = ws.cell(row=start + ri, column=ci + 1, value=_value(v, typ, f"{path}.rows[{ri}][{ci}]"))
                fmt = (cols[ci].get("format") if ci < len(cols) else None) or FORMATS.get(typ)
                if fmt:
                    cell.number_format = fmt
        if sheet.get("freeze"):
            ws.freeze_panes = sheet["freeze"]
        if sheet.get("autofilter") and ws.max_row > 1:
            ws.auto_filter.ref = ws.dimensions
        for rng in sheet.get("merge") or []:
            ws.merge_cells(rng)
        for ci, ch in enumerate(sheet.get("charts") or []):
            kind = CHARTS.get(ch.get("type", ""))
            if kind is None:
                raise DocError(f"{path}.charts[{ci}].type is bar, line or pie.")
            chart = kind()
            chart.title = ch.get("title")
            for vr in ch.get("values") or []:
                c1, r1, c2, r2 = range_boundaries(vr)
                chart.add_data(Reference(ws, min_col=c1, min_row=r1, max_col=c2, max_row=r2), titles_from_data=False)
            if ch.get("categories"):
                c1, r1, c2, r2 = range_boundaries(ch["categories"])
                chart.set_categories(Reference(ws, min_col=c1, min_row=r1, max_col=c2, max_row=r2))
            ws.add_chart(chart, ch.get("anchor", "H2"))
    wb.save(out)
    return len(workbook["sheets"])


def _shown(cell) -> str:
    """Значение ячейки так, как его показывает Excel, для частых форматов."""
    v = cell.value
    fmt = cell.number_format or "General"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (dt.datetime, dt.date)):
        if isinstance(v, dt.datetime) and ("h" in fmt.lower()):
            return v.strftime("%d.%m.%Y %H:%M")
        return v.strftime("%d.%m.%Y")
    if isinstance(v, (int, float)):
        if "%" in fmt:
            decimals = len(fmt.split(".")[1].split("%")[0]) if "." in fmt else 0
            return f"{v * 100:,.{decimals}f} %".replace(",", " ").replace(".", ",")
        if "0.0" in fmt or "#,##0" in fmt:
            decimals = len(fmt.split(".")[1].rstrip("_ ₽$€\"р.").rstrip()) if "." in fmt else 0
            decimals = min(decimals, 6)
            return f"{v:,.{decimals}f}".replace(",", " ").replace(".", ",")
        if isinstance(v, float):
            return f"{v:g}".replace(".", ",")
        return str(v)
    return "" if v is None else str(v)


def preview(path: str, max_rows: int = 500, max_cols: int = 50) -> dict:
    """Листы книги для предпросмотра: значения, сохранённые в файле, а при их
    отсутствии — формулы (R14)."""
    values = load_workbook(path, data_only=True)
    formulas = load_workbook(path, data_only=False)
    out = []
    for ws in values.worksheets:
        fws = formulas[ws.title]
        rows, cols = ws.max_row, ws.max_column
        cells = []
        for r in range(1, min(rows, max_rows) + 1):
            row = []
            for c in range(1, min(cols, max_cols) + 1):
                cell = ws.cell(row=r, column=c)
                if cell.value is None:
                    f = fws.cell(row=r, column=c).value
                    row.append({"f": f} if isinstance(f, str) and f.startswith("=") else {})
                    continue
                item = {"v": _shown(cell)}
                if isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                    item["n"] = cell.value
                row.append(item)
            cells.append(row)
        out.append({"name": ws.title, "rows": rows, "cols": cols, "cells": cells,
                    "merges": [str(m) for m in ws.merged_cells.ranges], "freeze": ws.freeze_panes or ""})
    return {"sheets": out}
