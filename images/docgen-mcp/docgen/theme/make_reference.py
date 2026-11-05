"""Pandoc samples of the theme default (FTR.NAB.CHAT-0003 R4): reference.docx and
reference.pptx of Pandoc with PT fonts. It runs at the build of the image.

python -m docgen.theme.make_reference <directory>
"""

import os
import re
import subprocess
import sys
import zipfile

from docx import Document

from .. import limits


def docx_reference(path: str) -> None:
    with open(path, "wb") as f:
        subprocess.run([limits.PANDOC, "--print-default-data-file", "reference.docx"], stdout=f, check=True)
    doc = Document(path)
    for style in doc.styles:
        try:
            font = style.font
        except AttributeError:
            continue
        name = style.name or ""
        if name.startswith("Heading") or name in ("Title", "Subtitle"):
            font.name = "PT Sans"
        elif name in ("Normal", "Body Text", "First Paragraph", "Compact", "Footnote Text", "Block Text"):
            font.name = "PT Serif"
    doc.save(path)


def pptx_reference(path: str) -> None:
    raw = path + ".raw"
    with open(raw, "wb") as f:
        subprocess.run([limits.PANDOC, "--print-default-data-file", "reference.pptx"], stdout=f, check=True)
    with zipfile.ZipFile(raw) as src, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if re.fullmatch(r"ppt/theme/theme\d+\.xml", item.filename):
                data = re.sub(rb'<a:latin typeface="[^"]*"', b'<a:latin typeface="PT Sans"', data)
            dst.writestr(item, data)
    os.remove(raw)


if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    docx_reference(os.path.join(out, "reference.docx"))
    pptx_reference(os.path.join(out, "reference.pptx"))
