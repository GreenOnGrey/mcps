# Personal MCP servers (FTR.INFRA.MCPS-0001): Jira, Confluence, GitLab, ClickHouse.
# The token of the user comes in every request from the proxy of Nabu; the servers store nothing.
# Jira and Confluence are one image: mcp-atlassian serves both, and a deployment enables one service.
FROM python:3.12-slim-trixie
ARG VERSION
# The upstream version is pinned here on purpose: a bump is a change of this file (FTR.INFRA.MCPS-0001 tech §4).
ARG MCP_ATLASSIAN_VERSION=0.24.6
WORKDIR /app
# Security fixes of the base image: a fixable HIGH blocks a release (images/publish.sh scans with Trivy).
RUN apt-get update && apt-get install -y --only-upgrade zlib1g && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "mcp-atlassian==${MCP_ATLASSIAN_VERSION}"
# The upstream writes attachments and caches to HOME: /tmp is the only writable place of the container.
# truststore is off: it replaces the SSL context of the process and breaks a private CA bundle,
# the chart passes REQUESTS_CA_BUNDLE and SSL_CERT_FILE instead.
ENV PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    MCP_ATLASSIAN_FILES_DIR=/tmp/files \
    MCP_ATLASSIAN_USE_SYSTEM_TRUSTSTORE=false
LABEL org.opencontainers.image.version="${VERSION}"
USER 10001
EXPOSE 8000
# Read-only twice: the flag of the server and READ_ONLY_MODE of the chart hide write tools at
# tools/list and refuse them by name at tools/call.
ENTRYPOINT ["mcp-atlassian", "--transport", "streamable-http", "--stateless", "--host", "0.0.0.0", \
  "--port", "8000", "--path", "/mcp", "--read-only"]
