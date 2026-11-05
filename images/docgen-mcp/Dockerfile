# docgen-mcp: the MCP server of documents (FTR.NAB.CHAT-0003 tech §2). The tag of the image is
# DOCGEN_MCP_VERSION from config/versions.env. Pandoc and fonts are the same packages
# as in the sandbox profile documents (the theme default on PT fonts).
FROM python:3.12-slim-trixie
ARG VERSION
RUN apt-get update && apt-get -y upgrade && apt-get install -y --no-install-recommends pandoc fontconfig \
      fonts-dejavu-core fonts-liberation2 fonts-paratype \
 && rm -rf /var/lib/apt/lists/* && fc-cache -f
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt
COPY docgen ./docgen
COPY tests ./tests
# Pandoc samples of the theme default and unit tests at the build; tests do not reach the image.
RUN python -m docgen.theme.make_reference /app/theme \
 && DOCGEN_REFERENCE_DIR=/app/theme python -m unittest discover -s tests && rm -rf tests
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/tmp DOCGEN_WORK_DIR=/tmp/docgen DOCGEN_REFERENCE_DIR=/app/theme
LABEL org.opencontainers.image.version="${VERSION}"
USER 10001
ENTRYPOINT ["python", "-m", "docgen.server"]
