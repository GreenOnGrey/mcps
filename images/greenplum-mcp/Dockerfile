# Greenplum MCP server of the stand (FTR.INFRA.MCPS-0002): our own server, because the credentials of
# the user must become the credentials of one session, and the server must refuse anything but one
# SELECT. The database package is pinned in requirements.txt: a bump is a change of that file.
FROM python:3.12-slim-trixie
ARG VERSION
WORKDIR /app
# Security fixes of the base image: a fixable HIGH blocks a release (images/publish.sh scans with Trivy).
RUN apt-get update && apt-get install -y --only-upgrade zlib1g libzstd1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# A slow mirror must not fail a release: the same reason as Acquire::Retries of apt.
ENV PIP_DEFAULT_TIMEOUT=60
RUN pip install --no-cache-dir --retries 5 -r requirements.txt
COPY greenplum_mcp ./greenplum_mcp
ENV PYTHONUNBUFFERED=1 HOME=/tmp
LABEL org.opencontainers.image.version="${VERSION}"
USER 10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "greenplum_mcp.server"]
