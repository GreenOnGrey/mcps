"""fill_template: шаблоны DOCX, XLSX и PPTX с полями Jinja2
(FTR.NAB.CHAT-0003 R3, tech §2.3). Окружение — песочница Jinja2 со строгими
полями: шаблон не исполняет код, отсутствующее поле — ошибка."""

from __future__ import annotations

import copy
import re

from jinja2 import StrictUndefined, TemplateError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment
from openpyxl import load_workbook
from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.formula.translate import Translator
from openpyxl.utils import get_column_letter
from pptx import Presentation

from . import limits
from .errors import DocError

ENV = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
FOR_ROW = re.compile(r"^\{%tr\s+for\s+(\w+)\s+in\s+(.+?)\s*%\}$")
END_ROW = re.compile(r"^\{%tr\s+endfor\s*%\}$")
CELL_REF = re.compile(r"^(\$?[A-Za-z]{1,3})?(\$?)(\d+)$")


def _render(text: str, data: dict) -> str:
    try:
        return ENV.from_string(text).render(**data)
    except UndefinedError as e:
        raise DocError(f"The template needs a field the data has not: {e.message}") from None
    except TemplateError as e:
        raise DocError(f"The template could not be filled: {e}") from None


def fill(template: str, data: dict, out: str) -> None:
    ext = template.lower().rsplit(".", 1)[-1]
    if ext in ("docm", "xlsm", "pptm"):
        raise DocError("macro-enabled templates are not supported.")
    if not isinstance(data, dict):
        raise DocError("data is an object.")
    limits.check_inline("data", data)
    if ext == "docx":
        from docxtpl import DocxTemplate
        doc = DocxTemplate(template)
        try:
            doc.render(data, jinja_env=ENV)
        except UndefinedError as e:
            raise DocError(f"The template needs a field the data has not: {e.message}") from None
        except TemplateError as e:
            raise DocError(f"The template could not be filled: {e}") from None
        doc.save(out)
    elif ext == "xlsx":
        fill_xlsx(template, data, out)
    elif ext == "pptx":
        fill_pptx(template, data, out)
    else:
        raise DocError("template is a .docx, .xlsx or .pptx file.")


def fill_xlsx(template: str, data: dict, out: str) -> None:
    wb = load_workbook(template)
    for ws in wb.worksheets:
        _rows(wb, ws, data)
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and ("{{" in cell.value or "{%" in cell.value):
                    cell.value = _cell(_render(cell.value, data))
    wb.save(out)


def _cell(text: str):
    """Число остаётся числом после подстановки."""
    t = text.strip()
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    if re.fullmatch(r"-?\d+\.\d+", t):
        return float(t)
    return text


class _Loop:
    """Строки цикла r…end шаблона (маркеры и тело) становятся n строками данных.

    Ссылки переносятся, как при вставке строк в Excel (R3): ниже цикла —
    сдвигаются, на строки шаблона — указывают на строки данных, а диапазон
    по ним растягивается на все строки данных.
    """

    def __init__(self, sheet: str, r: int, end: int, n: int):
        self.sheet, self.r, self.end, self.n = sheet, r, end, n
        self.delta = n - (end - r + 1)

    def row(self, x: int, last: bool) -> int:
        if x < self.r:
            return x
        if x > self.end:
            return x + self.delta
        return self.r + max(self.n, 1) - 1 if last else self.r

    def formula(self, text: str, own: bool) -> str:
        """Формула с перенесёнными ссылками на этот лист; own — формула на нём же."""
        try:
            tokens = Tokenizer(text).items
        except Exception:
            return text
        changed = False
        for t in tokens:
            if t.type != Token.OPERAND or t.subtype != Token.RANGE:
                continue
            ref = self.ref(t.value, own)
            if ref != t.value:
                t.value, changed = ref, True
        return "=" + "".join(t.value for t in tokens) if changed else text

    def ref(self, value: str, own: bool) -> str:
        sheet, sep, ref = value.rpartition("!")
        if sep:
            if sheet.strip("'").replace("''", "'") != self.sheet:
                return value
        elif not own:
            return value
        parts = ref.split(":")
        if len(parts) > 2:
            return value
        out = []
        for i, part in enumerate(parts):
            m = CELL_REF.match(part)
            if not m:
                out.append(part)
                continue
            last = len(parts) == 2 and i == 1
            out.append(f"{m.group(1) or ''}{m.group(2)}{self.row(int(m.group(3)), last)}")
        return sheet + sep + ":".join(out)


def _rows(wb, ws, data: dict) -> None:
    """Повтор строк между {%tr for x in list %} и {%tr endfor %} (решение 5 tech)."""
    r = 1
    while r <= ws.max_row:
        first = ws.cell(row=r, column=1).value
        m = FOR_ROW.match(first.strip()) if isinstance(first, str) else None
        if not m:
            r += 1
            continue
        end = next((e for e in range(r + 1, ws.max_row + 1)
                    if isinstance(ws.cell(row=e, column=1).value, str) and END_ROW.match(ws.cell(row=e, column=1).value.strip())), None)
        if end is None:
            raise DocError(f"{ws.title}: {{%tr for %}} in row {r} has no {{%tr endfor %}}.")
        var, expr = m.group(1), m.group(2)
        try:
            items = list(ENV.compile_expression(expr, undefined_to_none=False)(**data))
        except Exception:
            raise DocError(f"{ws.title}: the list {expr} is not in the data.") from None
        body = [[copy.copy(ws.cell(row=x, column=c)) for c in range(1, ws.max_column + 1)] for x in range(r + 1, end)]
        n_new = len(items) * len(body)
        n_old = end - r + 1
        loop = _Loop(ws.title, r, end, n_new)
        # формулы книги вне цикла: ссылки на этот лист — как после вставки строк
        for other in wb.worksheets:
            for row in other.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and cell.value.startswith("=") and not (other is ws and r <= cell.row <= end):
                        cell.value = loop.formula(cell.value, other is ws)
        # строки шаблона уже скопированы в body: освобождаем их до сдвига того, что ниже
        for x in range(r, end + 1):
            for c in range(1, ws.max_column + 1):
                ws.cell(row=x, column=c).value = None
        if n_new != n_old and end < ws.max_row:
            ws.move_range(f"A{end + 1}:{ws.cell(row=ws.max_row, column=ws.max_column).coordinate}", rows=loop.delta)
            for merged in list(ws.merged_cells.ranges):
                if merged.min_row > end:
                    merged.shift(row_shift=loop.delta)
        out = r
        for item in items:
            for tpl in body:
                for c, src in enumerate(tpl, start=1):
                    dst = ws.cell(row=out, column=c)
                    v = src.value
                    if isinstance(v, str) and ("{{" in v or "{%" in v):
                        v = _cell(_render(v, {**data, var: item}))
                    elif isinstance(v, str) and v.startswith("="):
                        # формула строки шаблона протягивается на строку данных, как в Excel
                        col = get_column_letter(c)
                        v = Translator(loop.formula(v, True), origin=f"{col}{r}").translate_formula(f"{col}{out}")
                    dst.value = v
                    if src.has_style:
                        dst._style = copy.copy(src._style)
                out += 1
        r = out


def fill_pptx(template: str, data: dict, out: str) -> None:
    prs = Presentation(template)
    for slide in prs.slides:
        for shape in slide.shapes:
            frames = []
            if shape.has_text_frame:
                frames.append(shape.text_frame)
            if getattr(shape, "has_table", False) and shape.has_table:
                frames += [cell.text_frame for row in shape.table.rows for cell in row.cells]
            for tf in frames:
                for p in tf.paragraphs:
                    text = "".join(r.text for r in p.runs)
                    if "{{" in text and p.runs:
                        p.runs[0].text = _render(text, data)
                        for r in p.runs[1:]:
                            r.text = ""
    prs.save(out)
