# docling-mcp в удалённом режиме поверх docling-serve (FTR.NAB.CMN-0006 tech §2.7).
# Версия пакета — DOCLING_MCP_VERSION из config/versions.env, она же тег образа.
FROM python:3.12-slim-trixie
ARG VERSION
RUN test -n "$VERSION" && pip install --no-cache-dir "docling-mcp==${VERSION}"
# Языки OCR: docling-mcp передаёт docling-serve только do_ocr, а сканы на
# русском требуют ocr_lang. Правка добавляет его из DOCLING_MCP_OCR_LANG;
# сборка падает, если место правки изменилось в новой версии.
COPY ocr_lang.py /tmp/ocr_lang.py
RUN python /tmp/ocr_lang.py && rm /tmp/ocr_lang.py
USER 10001
ENTRYPOINT ["docling-mcp-server"]
