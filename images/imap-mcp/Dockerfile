# imap-mcp — собственный MCP-сервер IMAP только для чтения (FTR.NAB.CMN-0006 R20–R22).
# Тег образа — IMAP_MCP_VERSION из config/versions.env.
FROM python:3.12-slim-trixie
ARG VERSION
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY imap_core.py server.py test_imap_core.py test_server.py ./
RUN python -m unittest test_imap_core test_server && rm test_imap_core.py test_server.py
ENV PYTHONUNBUFFERED=1 HOME=/tmp
LABEL org.opencontainers.image.version="${VERSION}"
USER 10001
ENTRYPOINT ["python", "/app/server.py"]
