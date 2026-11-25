# Web search MCP server of the stand (FTR.INFRA.MCPS-0003): our own server, because there is no
# MCP server for the DeepSeek Search and Fetch APIs. The platform key comes from the Secret
# mcp-deepseek-key, the address of the API is the setting of the stand.
FROM python:3.12-slim-trixie
ARG VERSION
WORKDIR /app
# Security fixes of the base image: a fixable HIGH blocks a release (images/publish.sh scans with Trivy).
RUN apt-get update && apt-get install -y --only-upgrade zlib1g libzstd1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# A slow mirror must not fail a release: the same reason as Acquire::Retries of apt.
ENV PIP_DEFAULT_TIMEOUT=60
RUN pip install --no-cache-dir --retries 5 -r requirements.txt
COPY search_mcp ./search_mcp
ENV PYTHONUNBUFFERED=1 HOME=/tmp
LABEL org.opencontainers.image.version="${VERSION}"
USER 10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "search_mcp.server"]
