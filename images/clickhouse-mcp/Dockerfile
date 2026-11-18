# ClickHouse MCP server of the stand (FTR.INFRA.MCPS-0001): the upstream mcp-clickhouse with our
# middleware, which turns the personal token of the user into the credentials of a ClickHouse query.
# The token is "login:password" of the ClickHouse user; the server adds readonly=1 and the limits of
# the chart to every query, so even a user with the right to write cannot change data.
FROM python:3.12-slim-trixie
ARG VERSION
# The upstream version is pinned in requirements.txt: a bump is a change of that file.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY clickhouse_mcp ./clickhouse_mcp
ENV PYTHONUNBUFFERED=1 HOME=/tmp
LABEL org.opencontainers.image.version="${VERSION}"
USER 10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "clickhouse_mcp.server"]
