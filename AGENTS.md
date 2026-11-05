# AGENTS.md — mcps

The optional MCP servers of the stand (`FTR.NAB.CMN-0006`, `FTR.NAB.CHAT-0003`, `FTR.INFRA.INST-0001`): their images and one Helm chart. `installer` installs the chart by `MCP_CHART_VERSION` and handles keys and checks. Specifications live in `hammurapi-specs`.

- Do not commit, tag or push unless asked.
- English for code, comments, docs, CI, scripts, logs, test names, and the messages and tool descriptions that services return. Russian stays in test data that exercises Russian text and the specifications in `hammurapi-specs`. Other Russian here is a leftover to translate.

## Contents

```text
charts/mcp-servers                the chart; one release, namespace mcp
charts/mcp-servers/files/servers  description of each server, read by installer with jq
charts/mcp-servers/files/dashboard.json   Grafana dashboard
images/<image>                    docling-mcp, imap-mcp, docgen-mcp, doc-convert; each has images/<image>/test.sh
images/publish.sh                 build, test, scan and push of the images
config/versions.env               versions of images and tools
tests/manifests.sh                checks of the chart and the descriptions
```

## Checks before a commit

A change is ready when every job of `.github/workflows/checks.yml` passes locally at the versions the workflow pins, plus the matching lines below. Read the workflow for the exact commands. Install a missing tool outside the repository at the pinned version. Name any job you could not run in your report, with the reason.

- Always: `shellcheck` on the file list of `checks.yml`, `actionlint`.
- `charts/**`, `config/**`: `bash tests/manifests.sh`.
- `images/**`: `images/publish.sh --test-only`.
- A chart change also has to survive an upgrade of the live release, which `helm template` cannot show. Compare with `kubectl patch --dry-run=server` on the live resource.
- Always, after the checks pass: run the `code-review` skill on the diff to be committed. Fix each finding, or name it in your report with the reason it stays. Ask the user when you doubt a finding.

## Rules

- **Shell, YAML and Helm templates are edited by hand.** After bulk edits, diff every file.
- Scripts are `set -euo pipefail`.
- **Images are published once per version**: `images/publish.sh` skips a tag that exists. A change of `images/imap-mcp` needs a new `IMAP_MCP_VERSION` (`DOCGEN_MCP_VERSION`, `DOC_CONVERT_VERSION` for the others) and the same tag in `charts/mcp-servers/values.yaml`, or a stand keeps running the old image.
- **The chart's values are what gets installed.** `installer` passes no image tags. `tests/manifests.sh` checks that they match `config/versions.env`.
- **A server's description is a contract with `installer`**: `name`, `valuesKey`, `service`, `deployments`, `secrets`, `keySecret`, `wantTools`, `oauth`, `catalogHint`. The file name equals `name`. A new server needs `<valuesKey>.enabled: false` in the values. `installer` enables it with the option `MCP_<NAME>`.
- The release name `mcp-servers` and the namespace `mcp` stay: a live stand upgrades in place.
- Keys come from Secrets of the namespace, never from values.
- A new image needs its package opened to the public, and a package created by `installer` needs this repository added under its **Manage Actions access**, or the release cannot push to it.

## Releases

A tag `vX.Y.Z` runs `release.yml`: images, then the chart `oci://ghcr.io/greenongrey/charts/mcp-servers` with version `X.Y.Z`. Existing versions are skipped. To move a stand, bump `MCP_CHART_VERSION` in `installer` and release it.
