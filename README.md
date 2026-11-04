# mcps

Optional MCP servers for [Nabu](https://github.com/GreenOnGrey/nabu), installed by
[installer](https://github.com/GreenOnGrey/installer).

| Server | What it does | Access |
| --- | --- | --- |
| `docling` | Converts documents to structured text | Platform key |
| `search` | Web search through SearXNG | Platform key |
| `yandex-mail` | Reads Yandex Mail over IMAP, read-only | Personal OAuth |
| `docgen` | Generates and converts documents and PDFs | Platform key |

## Install

`installer` enables each server with its own option, such as `MCP_DOCLING=true`. To install the chart directly:

```sh
helm install mcp-servers oci://ghcr.io/greenongrey/charts/mcp-servers \
  --version <version> -n mcp --create-namespace --set docling.enabled=true
```

The chart expects the access keys as Secrets in its namespace. `installer` creates them, so with plain Helm you create them yourself: their names are listed under `secrets` in `charts/mcp-servers/files/servers/<server>.json`.

## Layout

```text
charts/mcp-servers/             the Helm chart
charts/mcp-servers/files/servers/<server>.json   description of a server, read by installer
charts/mcp-servers/files/dashboard.json          Grafana dashboard
images/<image>/                 images built here: docling-mcp, imap-mcp, docgen-mcp, doc-convert
config/versions.env             image and tool versions
tests/manifests.sh              checks of the chart
```

## Release

A tag `vX.Y.Z` publishes the images whose tags do not exist yet, then the chart with version `X.Y.Z`. Nothing is deployed: a stand moves to the new chart when `MCP_CHART_VERSION` changes in `installer`.

## License

Apache-2.0, see [LICENSE](LICENSE).
