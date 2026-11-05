"""Adds to the remote converter of docling-mcp the parameter ocr_lang from DOCLING_MCP_OCR_LANG."""
import pathlib

import docling_mcp.tools.converters.remote as remote

path = pathlib.Path(remote.__file__)
src = path.read_text()
old = "            do_ocr=settings.do_ocr,\n"
new = old + (
    "            ocr_lang=[l.strip() for l in __import__('os').environ.get('DOCLING_MCP_OCR_LANG', '').split(',') if l.strip()] or None,\n"
)
if src.count(old) != 1:
    raise SystemExit("docling-mcp: the place of the ocr_lang patch changed in " + str(path))
path.write_text(src.replace(old, new))
print("patched", path)
