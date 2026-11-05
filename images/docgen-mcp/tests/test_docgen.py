"""Unit tests of docgen (FTR.NAB.CHAT-0003 qa: GEN-*, EDT-*, CNV-*, SEC-*).

python -m unittest discover -s tests — из каталога образа; нужен pandoc
(PANDOC), LibreOffice и Chromium не нужны: их проверяет test.sh.
"""

from __future__ import annotations

import http.server
import io
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SERVE_DIR = tempfile.mkdtemp(prefix="docgen-serve-")


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=SERVE_DIR, **kw)

    def do_PUT(self):  # noqa: N802 — an S3 upload by a presigned link
        n = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(n)
        with open(os.path.join(SERVE_DIR, "uploaded" + os.path.basename(self.path.split("?")[0])), "wb") as f:
            f.write(data)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


HTTPD = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
PORT = HTTPD.server_address[1]
threading.Thread(target=HTTPD.serve_forever, daemon=True).start()
os.environ["DOCGEN_S3_HOSTS"] = f"127.0.0.1:{PORT}"
os.environ.setdefault("DOCGEN_KEY", "test-key")
os.environ["DOCGEN_WORK_DIR"] = tempfile.mkdtemp(prefix="docgen-work-")

from docgen import converting, documents, editing, fetch, htmlscan, pdfs, sheets, templates  # noqa: E402
from docgen.errors import DocError  # noqa: E402
from docgen.theme import make_reference  # noqa: E402

HAS_PANDOC = shutil.which(os.environ.get("PANDOC", "pandoc")) is not None


def url(name: str, rel: str = "") -> str:
    u = f"http://127.0.0.1:{PORT}/{name}"
    return u + (f"#nabu-path={rel}" if rel else "")


def serve(name: str, data: bytes) -> str:
    with open(os.path.join(SERVE_DIR, name), "wb") as f:
        f.write(data)
    return url(name)


class Fetch(unittest.TestCase):
    def test_split_and_safe_paths(self):
        self.assertEqual(fetch.split("http://h/x/a.png?sig#nabu-path=charts/q3.png"), ("http://h/x/a.png?sig", "charts/q3.png"))
        self.assertEqual(fetch.split("http://h/x/a.png?sig#nabu-path=../../etc/passwd")[1], "a.png")
        self.assertEqual(fetch.safe_rel("/etc/passwd"), "")

    def test_only_s3_of_the_stand(self):
        # SEC-13: docgen goes only by links of the S3 of the stand
        with self.assertRaises(DocError):
            fetch.check_host("http://169.254.169.254/latest/meta-data/")
        with self.assertRaises(DocError):
            fetch.check_host("file:///etc/passwd")

    def test_download_limit(self):
        u = serve("big.bin", b"x" * 2048)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(DocError):
                fetch.download(u, d, 1024)
            p = fetch.download(u + "#nabu-path=data/big.bin", d, 4096)
            self.assertTrue(p.endswith("data/big.bin"))


class Options(unittest.TestCase):
    def test_options_block(self):
        opts, md = documents.split_options("---\ntitle: Отчёт\npage: {size: A4, orientation: landscape, margins: 20}\ntoc: true\n---\n# Q3\n")
        self.assertEqual(opts["title"], "Отчёт")
        self.assertEqual(md, "# Q3\n")
        w, h, m = documents.page_mm(opts)
        self.assertEqual((w, h, m["left"]), (297, 210, 20.0))

    def test_unknown_option(self):
        with self.assertRaises(DocError) as e:
            documents.split_options("---\ncolor: red\n---\nx")
        self.assertIn("color", str(e.exception))

    def test_markdown_argument(self):
        # GEN-16: the text of the document, not a shell expression; a file of the space is read as UTF-8
        self.assertEqual(documents.inline("# Заголовок\n"), "# Заголовок\n")
        for text in ("`npm i` ставит пакеты", "$x$ — переменная", "```sh\ncat a.md\n```", "$5 за штуку"):
            self.assertEqual(documents.inline(text), text)
        for expr in ("$(cat report.md)", "`cat report.md`", "${DOC}", "$DOC"):
            with self.assertRaises(DocError) as e:
                documents.inline(expr)
            self.assertIn("source", str(e.exception))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.md")
            with open(p, "wb") as f:
                f.write("\ufeff# Отчёт\n".encode())
            self.assertEqual(documents.read_source(p), "# Отчёт\n")
            with open(p, "wb") as f:
                f.write(b"\xff\xfe\x00")
            with self.assertRaises(DocError):
                documents.read_source(p)

    def test_inline_limit(self):
        # SEC-09, R12: Markdown, the JSON of a table and the data of a template — up to 4 MB
        with self.assertRaises(DocError):
            documents.inline("x" * (5 << 20))
        big = {"sheets": [{"name": "S", "rows": [["x" * 1024] for _ in range(5 << 10)]}]}
        with self.assertRaises(DocError) as e:
            sheets.render(big, "/tmp/never.xlsx")
        self.assertIn("workbook", str(e.exception))
        with tempfile.TemporaryDirectory() as d:
            tpl = os.path.join(d, "t.xlsx")
            from openpyxl import Workbook
            Workbook().save(tpl)
            with self.assertRaises(DocError) as e:
                templates.fill(tpl, {"rows": ["x" * 1024] * (5 << 10)}, os.path.join(d, "o.xlsx"))
            self.assertIn("data", str(e.exception))

    def test_reference_layout_kept(self):
        # GEN-03, R4: the page and the footers of a reference stay; only explicit options change them
        from docx import Document
        from docx.shared import Mm
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.docx")
            doc = Document()
            s = doc.sections[0]
            s.left_margin, s.page_width = Mm(12), Mm(200)
            s.footer.paragraphs[0].text = "ООО «Ромашка»"
            doc.add_paragraph("x")
            doc.save(path)
            documents.layout_docx(path, {}, own=False)
            s = Document(path).sections[0]
            self.assertEqual((round(s.left_margin.mm), round(s.page_width.mm), s.footer.paragraphs[0].text), (12, 200, "ООО «Ромашка»"))
            documents.layout_docx(path, {"page": {"margins": {"left": 25}}}, own=False)
            s = Document(path).sections[0]
            self.assertEqual((round(s.left_margin.mm), round(s.page_width.mm), s.footer.paragraphs[0].text), (25, 200, "ООО «Ромашка»"))
            documents.layout_docx(path, {})
            self.assertEqual(round(Document(path).sections[0].left_margin.mm), 30)


@unittest.skipUnless(HAS_PANDOC, "pandoc is required")
class Documents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = tempfile.mkdtemp(prefix="docgen-ref-")
        make_reference.docx_reference(os.path.join(cls.ref, "reference.docx"))
        make_reference.pptx_reference(os.path.join(cls.ref, "reference.pptx"))
        documents.REFERENCE_DIR = cls.ref

    def test_docx(self):
        # GEN-01, GEN-04, GEN-05
        from docx import Document
        png = _png()
        md = ("---\ntitle: Итоги\npage: {orientation: landscape}\nfooter: \"Стр. {page} из {pages}\"\n---\n"
              "# Раздел\n\nТекст[^1].\n\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\n![график](charts/q3.png)\n\n"
              "<!-- pagebreak -->\n\n## Ещё\n\n[^1]: Сноска.\n")
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "charts"))
            with open(os.path.join(d, "charts", "q3.png"), "wb") as f:
                f.write(png)
            out, warnings = documents.render(d, md, "docx", None)
            doc = Document(out)
            self.assertEqual(len(doc.tables), 1)
            self.assertEqual(doc.tables[0].cell(2, 1).text, "4")
            self.assertTrue(any(p.style.name.startswith("Heading") for p in doc.paragraphs))
            self.assertGreater(doc.sections[0].page_width, doc.sections[0].page_height)
            self.assertIn('w:br w:type="page"', doc.element.xml)
            self.assertTrue(doc.inline_shapes)
            self.assertIn("NUMPAGES", doc.sections[0].footer._element.xml)

    def test_pptx(self):
        # GEN-03
        from pptx import Presentation
        md = "## Один\n\nТекст\n\n::: notes\nЗаметка\n:::\n\n## Два\n\n## Три\n"
        with tempfile.TemporaryDirectory() as d:
            out, _ = documents.render(d, md, "pptx", None)
            prs = Presentation(out)
            self.assertEqual(len(prs.slides), 3)
            self.assertIn("Заметка", prs.slides[0].notes_slide.notes_text_frame.text)

    def test_reference(self):
        # GEN-14: the styles of a reference document
        from docx import Document
        ref = os.path.join(self.ref, "reference.docx")
        brand = os.path.join(self.ref, "brand.docx")
        # by the id: pandoc 3.1 names the style «Heading 1», later versions «heading 1»
        def heading(doc):
            return next(s for s in doc.styles if s.style_id == "Heading1")
        d = Document(ref)
        heading(d).font.name = "Brand Sans"
        d.save(brand)
        with tempfile.TemporaryDirectory() as w:
            out, _ = documents.render(w, "# Заголовок\n", "docx", brand)
            self.assertEqual(heading(Document(out)).font.name, "Brand Sans")


class Sheets(unittest.TestCase):
    def test_render_and_preview(self):
        # GEN-07, GEN-09, PRV-05 (a formula without a value)
        wb = {"sheets": [{"name": "Смета", "columns": [{"header": "Позиция"}, {"header": "Кол-во", "type": "integer"},
                                                         {"header": "Сумма", "type": "currency", "width": 16}, {"header": "Дата", "type": "date"}],
                          "rows": [["Стеллаж", 12, 186000, "2026-11-03"], ["Паллета", 40, 32000, "2026-11-03"], ["Итого", None, "=SUM(C2:C3)", None]],
                          "freeze": "A2", "autofilter": True, "merge": ["A5:B5"],
                          "charts": [{"type": "bar", "categories": "A2:A3", "values": ["C2:C3"], "anchor": "F2"}]},
                         {"name": "Работы", "rows": [["x"]]}]}
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "a.xlsx")
            self.assertEqual(sheets.render(wb, out), 2)
            from openpyxl import load_workbook
            ws = load_workbook(out)["Смета"]
            self.assertEqual(ws["C4"].value, "=SUM(C2:C3)")
            self.assertEqual(ws.freeze_panes, "A2")
            self.assertEqual(len(ws._charts), 1)
            p = sheets.preview(out)
            self.assertEqual([s["name"] for s in p["sheets"]], ["Смета", "Работы"])
            row4 = p["sheets"][0]["cells"][3]
            self.assertEqual(row4[2], {"f": "=SUM(C2:C3)"})
            self.assertEqual(p["sheets"][0]["cells"][1][3]["v"], "03.11.2026")
            self.assertEqual(p["sheets"][0]["cells"][1][2]["v"], "186 000,00")

    def test_bad_schema(self):
        with self.assertRaises(DocError) as e:
            sheets.render({"sheets": [{"name": "A", "columns": [{"type": "money2"}]}]}, "/tmp/x.xlsx")
        self.assertIn("columns[0].type", str(e.exception))
        with self.assertRaises(DocError) as e:
            sheets.render({"sheets": [{"name": "A", "columns": [{"header": "a"}], "rows": [[1, 2]]}]}, "/tmp/x.xlsx")
        self.assertIn("rows[0]", str(e.exception))


class Templates(unittest.TestCase):
    def test_docx_fields_and_rows(self):
        # GEN-10, GEN-11
        from docx import Document
        with tempfile.TemporaryDirectory() as d:
            tpl = os.path.join(d, "t.docx")
            doc = Document()
            doc.add_paragraph("Клиент: {{ client }}, дата {{ date }}")
            t = doc.add_table(rows=3, cols=2)
            t.cell(0, 0).text = "{%tr for p in items %}"
            t.cell(1, 0).text = "{{ p.name }}"
            t.cell(1, 1).text = "{{ p.qty }}"
            t.cell(2, 0).text = "{%tr endfor %}"
            doc.save(tpl)
            out = os.path.join(d, "o.docx")
            items = [{"name": f"Позиция {i}", "qty": i} for i in range(7)]
            templates.fill(tpl, {"client": "Ромашка", "date": "23.10.2026", "items": items}, out)
            res = Document(out)
            self.assertIn("Ромашка", res.paragraphs[0].text)
            self.assertEqual(len(res.tables[0].rows), 7)
            with self.assertRaises(DocError) as e:
                templates.fill(tpl, {"client": "x", "items": []}, out)
            self.assertIn("date", str(e.exception))

    def test_sandboxed(self):
        # GEN-13: a template does not run code
        with self.assertRaises(DocError):
            templates._render("{{ ''.__class__.__mro__[1].__subclasses__() }}", {})
        with self.assertRaises(DocError):
            templates._render("{% import os %}", {})

    def test_xlsx_rows(self):
        # GEN-12
        from openpyxl import Workbook, load_workbook
        with tempfile.TemporaryDirectory() as d:
            tpl = os.path.join(d, "t.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.append(["Позиция", "Кол-во"])
            ws.append(["{%tr for p in items %}"])
            ws.append(["{{ p.name }}", "{{ p.qty }}"])
            ws.append(["{%tr endfor %}"])
            ws.append(["Клиент: {{ client }}"])
            wb.save(tpl)
            out = os.path.join(d, "o.xlsx")
            templates.fill(tpl, {"client": "Ромашка", "items": [{"name": "a", "qty": 1}, {"name": "b", "qty": 2}, {"name": "c", "qty": 3}]}, out)
            ws = load_workbook(out).active
            self.assertEqual([ws.cell(row=r, column=1).value for r in range(2, 6)], ["a", "b", "c", "Клиент: Ромашка"])
            self.assertEqual(ws["B3"].value, 2)

    def test_xlsx_rows_formulas(self):
        # GEN-12, R3: rows of a loop are inserted as in Excel — totals stretch, references below move
        from openpyxl import Workbook, load_workbook
        with tempfile.TemporaryDirectory() as d:
            tpl = os.path.join(d, "t.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.title = "Счёт"
            ws.append(["Позиция", "Кол-во", "Цена", "Сумма"])            # 1
            ws.append(["{%tr for p in items %}"])                         # 2
            ws.append(["{{ p.name }}", "{{ p.qty }}", "{{ p.price }}", "=B3*C3"])  # 3
            ws.append(["{%tr endfor %}"])                                 # 4
            ws.append(["Итого", "=SUM(B3:B3)", None, "=SUM(D2:D4)"])      # 5
            ws.append(["Подпись", None, None, "=D5*2"])                   # 6
            ws.merge_cells("A7:B7")
            ws["A7"] = "Подвал"
            other = wb.create_sheet("Свод")
            other["A1"] = "='Счёт'!D5"
            wb.save(tpl)
            out = os.path.join(d, "o.xlsx")
            items = [{"name": "a", "qty": 1, "price": 10}, {"name": "b", "qty": 2, "price": 20}, {"name": "c", "qty": 3, "price": 30}]
            templates.fill(tpl, {"items": items}, out)
            res = load_workbook(out)
            ws = res["Счёт"]
            self.assertEqual([ws.cell(row=r, column=1).value for r in range(2, 7)], ["a", "b", "c", "Итого", "Подпись"])
            self.assertEqual([ws.cell(row=r, column=4).value for r in range(2, 5)], ["=B2*C2", "=B3*C3", "=B4*C4"])
            self.assertEqual(ws["B5"].value, "=SUM(B2:B4)")
            self.assertEqual(ws["D5"].value, "=SUM(D2:D4)")
            self.assertEqual(ws["D6"].value, "=D5*2")
            self.assertEqual(ws["A7"].value, "Подвал")
            self.assertEqual([str(m) for m in ws.merged_cells.ranges], ["A7:B7"])
            self.assertEqual(res["Свод"]["A1"].value, "='Счёт'!D5")
            # six items: the rows below move down
            templates.fill(tpl, {"items": items * 2}, out)
            res = load_workbook(out)
            ws = res["Счёт"]
            self.assertEqual((ws["A8"].value, ws["B8"].value, ws["D9"].value, ws["D7"].value), ("Итого", "=SUM(B2:B7)", "=D8*2", "=B7*C7"))
            self.assertEqual([str(m) for m in ws.merged_cells.ranges], ["A10:B10"])
            self.assertEqual(res["Свод"]["A1"].value, "='Счёт'!D8")
            # one item: the body shrinks, the references follow
            templates.fill(tpl, {"items": items[:1]}, out)
            ws = load_workbook(out)["Счёт"]
            self.assertEqual((ws["A3"].value, ws["B3"].value, ws["D4"].value), ("Итого", "=SUM(B2:B2)", "=D3*2"))
            self.assertEqual([str(m) for m in ws.merged_cells.ranges], ["A5:B5"])

    def test_macro_templates(self):
        # SEC-07
        with self.assertRaises(DocError):
            templates.fill("/tmp/t.xlsm", {}, "/tmp/o.xlsm")


class Editing(unittest.TestCase):
    def test_replace_keeps_format(self):
        # EDT-01, EDT-02
        from docx import Document
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "c.docx")
            doc = Document()
            p = doc.add_paragraph("Срок оплаты — ")
            r = p.add_run("10 д")
            r.bold = True
            p.add_run("ней.")
            doc.save(src)
            out = os.path.join(d, "o.docx")
            done = editing.edit(src, [{"op": "replace_text", "find": "10 дней", "replace": "30 дней"}], out)
            res = Document(out).paragraphs[0]
            self.assertEqual(done["replaced"], 1)
            self.assertEqual(res.text, "Срок оплаты — 30 дней.")
            self.assertTrue(res.runs[1].bold)
            self.assertEqual(res.runs[1].text, "30 дней")

    def test_xlsx_ops(self):
        # EDT-03, EDT-04
        from openpyxl import Workbook, load_workbook
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "b.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.title = "Расходы"
            ws.append(["a", 1])
            ws.append(["b", 2])
            ws["D1"] = "=SUM(B1:B2)"
            wb.save(src)
            out = os.path.join(d, "o.xlsx")
            editing.edit(src, [{"op": "append_rows", "sheet": "Расходы", "rows": [["c", 3], ["d", 4]]},
                               {"op": "set_cells", "sheet": "Расходы", "range": "B1:B1", "values": [[10]]},
                               {"op": "add_sheet", "name": "Итоги"}], out)
            wb = load_workbook(out)
            ws = wb["Расходы"]
            self.assertEqual((ws["A3"].value, ws["A4"].value, ws["B1"].value, ws["D1"].value), ("c", "d", 10, "=SUM(B1:B2)"))
            self.assertEqual(wb.sheetnames, ["Расходы", "Итоги"])

    def test_wrong_op(self):
        with self.assertRaises(DocError):
            editing.edit("/tmp/x.docx", [{"op": "set_cells"}], "/tmp/y.docx")


class Html(unittest.TestCase):
    def test_external_addresses(self):
        # SEC-01 (the warnings part; the block itself is Chromium's)
        page = b"""<html><head><link rel=stylesheet href="https://cdn.example.com/a.css">
<style>@import url(http://grafana.observability.svc/x.css); body{background:url('//evil/x.png')}</style>
<meta http-equiv="refresh" content="0; url=http://example.org/"></head><body>
<img src="http://169.254.169.254/latest/meta-data/"><iframe src="file:///etc/passwd"></iframe>
<img src="data:image/png;base64,AAAA"><img src="img/logo.png">
<script>fetch('http://api.nabu.svc:8081/x')</script></body></html>"""
        found = htmlscan.external(page)
        for u in ["https://cdn.example.com/a.css", "http://grafana.observability.svc/x.css", "//evil/x.png",
                  "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd", "http://api.nabu.svc:8081/x", "http://example.org/"]:
            self.assertIn(u, found)
        self.assertNotIn("img/logo.png", found)
        self.assertFalse(any(u.startswith("data:") for u in found))

    def test_chromium_options(self):
        # CNV-05: window.status is awaited only when the page sets it — Gotenberg would wait until its timeout
        o = converting.chromium({}, b"<script>draw(); window.status = 'ready'</script>")
        self.assertTrue(o["preferCssPageSize"])
        self.assertEqual(o["waitForExpression"], "window.status === 'ready'")
        o = converting.chromium({}, b"<h1>Strategy</h1>")
        self.assertNotIn("waitForExpression", o)
        self.assertEqual(o["waitDelay"], "1s")
        self.assertIn("waitForExpression", converting.chromium({"waitForReady": True}, b"<h1>x</h1>"))
        o = converting.chromium({"paper": "A4", "landscape": True, "margins": {"top": 25.4}})
        self.assertEqual((o["landscape"], o["marginTop"]), (True, 1.0))
        self.assertNotIn("preferCssPageSize", o)


class Archives(unittest.TestCase):
    def _zip(self, files: dict, path: str, symlink: str = ""):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for n, data in files.items():
                zf.writestr(n, data)
            if symlink:
                info = zipfile.ZipInfo(symlink)
                info.external_attr = (0o120777 << 16)
                zf.writestr(info, "/etc/passwd")

    def test_site(self):
        # CNV-04
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "r.zip")
            self._zip({"site/index.html": b'<link href="css/style.css"><img src="img/logo.png">',
                       "site/css/style.css": b"body{background:url('../img/bg.png')} h1{background:url(./icons/h.svg)}",
                       "site/css/icons/h.svg": b"<svg/>", "site/img/logo.png": b"png", "site/img/bg.png": b"png"}, p)
            index, assets = converting.unpack(p, d)
            self.assertIn(b'href="style.css"', index)
            self.assertEqual(sorted(n for n, _ in assets), ["bg.png", "h.svg", "logo.png", "style.css"])
            # R8: links inside a stylesheet are relative to its folder
            self.assertEqual(dict(assets)["style.css"], b"body{background:url('bg.png')} h1{background:url(h.svg)}")
            self.assertEqual(dict(assets)["logo.png"], b"png")

    def test_unsafe(self):
        # SEC-08
        with tempfile.TemporaryDirectory() as d:
            for files, link in [({"index.html": b"x", "../../etc/x": b"x"}, ""), ({"index.html": b"x"}, "link"),
                                ({"index.html": b"x", "bomb.txt": b"\0" * (50 << 20)}, ""), ({"a.html": b"x"}, "")]:
                p = os.path.join(d, "u.zip")
                self._zip(files, p, link)
                with self.assertRaises(DocError):
                    converting.unpack(p, d)


class Pdf(unittest.TestCase):
    def _pdf(self, path: str, pages: int):
        from pypdf import PdfWriter
        w = PdfWriter()
        for _ in range(pages):
            w.add_blank_page(width=200, height=200)
        with open(path, "wb") as f:
            w.write(f)

    def test_ops(self):
        # CNV-08
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.pdf"), os.path.join(d, "b.pdf")
            self._pdf(a, 6)
            self._pdf(b, 2)
            self.assertEqual(pdfs.run("merge", [a, b], "", 90)[2], 8)
            data, ext, n = pdfs.run("split", [a], "1-3,5", 90)
            self.assertEqual((ext, n), ("zip", 4))
            self.assertEqual(sorted(zipfile.ZipFile(io.BytesIO(data)).namelist()), ["part-1.pdf", "part-2.pdf"])
            self.assertEqual(pdfs.run("extract", [a], "4-6", 90)[2], 3)
            from pypdf import PdfReader
            data, _, _ = pdfs.run("rotate", [a], "2", 90)
            self.assertEqual(PdfReader(io.BytesIO(data)).pages[1].rotation, 90)
            with self.assertRaises(DocError):
                pdfs.run("extract", [a], "7", 90)


class Server(unittest.TestCase):
    def _call(self, app, path, headers=(), body=b"", method="POST"):
        import asyncio
        sent = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(m):
            sent.append(m)

        scope = {"type": "http", "path": path, "method": method, "headers": [(k.encode(), v.encode()) for k, v in headers]}
        asyncio.run(app(scope, receive, send))
        status = sent[0]["status"]
        return status, b"".join(m.get("body", b"") for m in sent[1:])

    def test_auth_and_preview(self):
        # SEC-12, PRV-04
        from docgen import server

        async def inner(scope, receive, send):
            raise AssertionError("not reached")

        app = server.with_auth(inner)
        self.assertEqual(self._call(app, "/healthz", method="GET")[0], 200)
        self.assertEqual(self._call(app, "/mcp")[0], 401)
        self.assertEqual(self._call(app, "/mcp", [("authorization", "Bearer wrong")])[0], 401)
        with tempfile.TemporaryDirectory() as d:
            xl = os.path.join(d, "b.xlsx")
            sheets.render({"sheets": [{"name": "Лист", "rows": [["a", 1]]}]}, xl)
            with open(xl, "rb") as f:
                u = serve("b.xlsx", f.read())
        status, body = self._call(app, "/internal/xlsx-preview", [("authorization", "Bearer test-key")], json.dumps({"url": u}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sheets"][0]["name"], "Лист")
        status, _ = self._call(app, "/internal/xlsx-preview", [("authorization", "Bearer test-key")],
                               json.dumps({"url": "http://169.254.169.254/x.xlsx"}).encode())
        self.assertEqual(status, 422)

    def test_multipart(self):
        from docgen import gotenberg
        body, ctype = gotenberg.multipart({"nativePageRanges": "1-50"}, [("files", "a.docx", b"DATA")])
        self.assertIn(b'name="nativePageRanges"\r\n\r\n1-50', body)
        self.assertIn(b'filename="a.docx"', body)
        self.assertTrue(ctype.startswith("multipart/form-data; boundary="))


def _png() -> bytes:
    import base64
    return base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


if __name__ == "__main__":
    unittest.main()
