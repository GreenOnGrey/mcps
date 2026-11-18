# GitLab MCP server of the stand (FTR.INFRA.MCPS-0001): zereight/gitlab-mcp, which reads the
# token of the user from the header of every request (REMOTE_AUTHORIZATION). The built-in
# /api/v4/mcp of GitLab needs 19.5, and the stand has 17.11: see tech §4.
FROM node:24-slim
ARG VERSION
# The upstream version is pinned here on purpose: a bump is a change of this file.
ARG MCP_GITLAB_VERSION=2.3.29
# The package is installed at the build, not by npx at the start: the container has no cache to write.
RUN npm install -g "@zereight/mcp-gitlab@${MCP_GITLAB_VERSION}" && npm cache clean --force
# The upstream writes downloaded artifacts to GITLAB_DOWNLOAD_DIR: /tmp is the only writable place.
ENV HOME=/tmp NODE_ENV=production
LABEL org.opencontainers.image.version="${VERSION}"
USER 10001
EXPOSE 8000
ENTRYPOINT ["mcp-gitlab"]
