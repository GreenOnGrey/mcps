# docling-mcp in remote mode on top of docling-serve (FTR.NAB.CMN-0006 tech §2.7).
# The version of the package is DOCLING_MCP_VERSION from config/versions.env, also the tag of the image.
FROM python:3.12-slim-trixie
ARG VERSION
RUN test -n "$VERSION" && pip install --no-cache-dir "docling-mcp==${VERSION}"
# OCR languages: docling-mcp passes only do_ocr to docling-serve, while scans in
# Russian need ocr_lang. The patch adds it from DOCLING_MCP_OCR_LANG;
# the build fails if the place of the patch changed in a new version.
COPY ocr_lang.py /tmp/ocr_lang.py
RUN python /tmp/ocr_lang.py && rm /tmp/ocr_lang.py
USER 10001
ENTRYPOINT ["docling-mcp-server"]
