# mcps

Optional MCP servers for [Nabu](https://github.com/GreenOnGrey/nabu), installed by
[installer](https://github.com/GreenOnGrey/installer).

| Server | What it does | Access |
| --- | --- | --- |
| `docling` | Converts documents to structured text | Platform key |
| `search` | Web search through SearXNG | Platform key |
| `yandex-mail` | Reads Yandex Mail over IMAP, read-only | Personal OAuth |
| `docgen` | Generates and converts documents and PDFs | Platform key |
| `jira` | Reads Jira issues, projects and sprints, read-only | Personal OAuth (Cloud) or token (Data Center) |
| `confluence` | Reads Confluence pages, spaces and comments, read-only | Personal OAuth (Cloud) or token (Data Center) |
| `gitlab` | Reads projects, merge requests, issues and pipelines, read-only | Personal token (`read_api`) |
| `clickhouse` | Runs read-only `SELECT` queries | Personal login and password |

## Install

`installer` enables each server with its own option, such as `MCP_DOCLING=true`. To install the chart directly:

```sh
helm install mcp-servers oci://ghcr.io/greenongrey/charts/mcp-servers \
  --version <version> -n mcp --create-namespace --set docling.enabled=true
```

The platform servers expect their access keys as Secrets in the chart namespace. `installer` creates them, so with plain Helm you create them yourself: their names are listed under `secrets` in `charts/mcp-servers/files/servers/<server>.json`.

The personal servers (`yandex-mail`, `jira`, `confluence`, `gitlab`, `clickhouse`) have no key and no Secret: the token of the user comes in the header of every request from the proxy of Nabu, and the server keeps nothing. They do need the address of the company system, which is a setting of the stand — `jira.url`, `confluence.url`, `gitlab.url`, `clickhouse.host` — and, for a system inside the company network, its network in `egressAllow`.

## Layout

```text
charts/mcp-servers/             the Helm chart
charts/mcp-servers/files/servers/<server>.json   description of a server, read by installer
charts/mcp-servers/files/dashboard.json          Grafana dashboard
images/<image>/                 images built here: docling-mcp, imap-mcp, docgen-mcp, doc-convert,
                                atlassian-mcp, gitlab-mcp, clickhouse-mcp
config/versions.env             image and tool versions
tests/manifests.sh              checks of the chart
```

## Personal servers

The upstream packages are pinned in `images/<image>/Dockerfile`; the images themselves are ours, because the chart's rules (a non-root user, a read-only root filesystem, `/healthz`, a Trivy scan) cannot be applied to a foreign image:

| Image | Upstream | Why ours |
| --- | --- | --- |
| `atlassian-mcp` | `mcp-atlassian` | Reads the user token from the request header; read-only mode hides write tools |
| `gitlab-mcp` | `@zereight/mcp-gitlab` | `REMOTE_AUTHORIZATION` takes the token per request; the built-in `/api/v4/mcp` of GitLab needs 19.5 |
| `clickhouse-mcp` | `mcp-clickhouse` | Our middleware turns the personal token into the credentials of a query and adds `readonly=1` |

## Release

A tag `vX.Y.Z` publishes the images whose tags do not exist yet, then the chart with version `X.Y.Z`. Nothing is deployed: a stand moves to the new chart when `MCP_CHART_VERSION` changes in `installer`.

## License

Apache-2.0, see [LICENSE](LICENSE).
