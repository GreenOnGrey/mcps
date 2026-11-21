# mcps

Optional MCP servers for [Nabu](https://github.com/GreenOnGrey/nabu), installed by
[installer](https://github.com/GreenOnGrey/installer). A server is a [Model Context Protocol](https://modelcontextprotocol.io)
server: Nabu shows its methods (tools) to the agent of a user, the agent calls them, the server answers.

| Server | What it does | Methods | Access |
| --- | --- | --- | --- |
| [`docling`](#docling) | Converts documents to structured text and Markdown | 5 | Platform key |
| [`search`](#search) | Web search through SearXNG and reading a page | 2 | Platform key |
| [`yandex-mail`](#yandex-mail) | Reads a Yandex Mail mailbox over IMAP, read-only | 4 | Personal OAuth |
| [`docgen`](#docgen) | Creates, fills, edits and converts documents and PDFs | 6 | Platform key |
| [`jira`](#jira-and-confluence) | Reads Jira issues, projects, boards and sprints, read-only | 37 | Personal OAuth (Cloud) or token (Data Center) |
| [`confluence`](#jira-and-confluence) | Reads Confluence pages, comments and attachments, read-only | 20 | Personal OAuth (Cloud) or token (Data Center) |
| [`gitlab`](#gitlab) | Reads projects, merge requests, issues and pipelines, read-only | 95 | Personal token (`read_api`) |
| [`clickhouse`](#clickhouse) | Runs read-only `SELECT` queries | 3 | Personal login and password |

Every server sits behind the auth proxy of the chart (`nginx`): the platform servers check the platform
key, the personal ones pass the token of the user in the request header and keep nothing.

## Methods

### `docling`

Upstream [docling-mcp](https://pypi.org/project/docling-mcp/) 3.3.0 over `docling-serve`: the document is
parsed once, then the agent reads it by anchors instead of loading the whole text.

| Method | What it does |
| --- | --- |
| `convert_document_into_docling_document` | Parses a document of the space (PDF, DOCX, PPTX, XLSX, HTML, images) into a Docling document with layout, tables and OCR |
| `export_docling_document_to_markdown` | Exports the parsed document to Markdown |
| `get_overview_of_document_anchors` | The tree of the document: chapters, tables and figures with their anchors |
| `search_for_text_in_document_anchors` | Finds a text in the document and returns the anchors around it |
| `get_text_of_document_item_at_anchor` | The text of one item of the document by its anchor |

### `search`

Upstream [mcp-searxng](https://github.com/ihor-sokoliuk/mcp-searxng) 2.6.0 over the stand's
[SearXNG](https://searxng.github.io/): no search engine account, and the engines are a setting of the stand.

| Method | Arguments | What it does |
| --- | --- | --- |
| `searxng_web_search` | `query`, `pageno` | Searches the web: title, address and a fragment of each result |
| `web_url_read` | `url`, `startChar`, `maxLength` | Reads a page as text; a private address needs `allowPrivateUrls` and its network in `egressAllow` |

### `yandex-mail`

Our own [imap-mcp](images/imap-mcp) (read-only IMAP): the OAuth token of the user comes with every call,
the server connects to `imap.yandex.com` on behalf of the user and keeps nothing. Messages stay unread;
the body of a letter is trimmed to `max_chars`.

| Method | Arguments | What it does |
| --- | --- | --- |
| `list_folders` | — | The folders of the mailbox with their special role: Inbox, Sent, Drafts, Trash, Spam |
| `search_messages` | `folder`, `sender`, `recipient`, `subject`, `text`, `since`, `before`, `unseen`, `limit` (≤ 50), `offset` | Finds messages newest first; all conditions are optional and joined with AND; dates are `YYYY-MM-DD` |
| `read_message` | `folder`, `uid`, `max_chars` | One message: headers, text without HTML markup and the list of attachments (names and sizes) |
| `read_thread` | `thread_id`, `max_chars`, `since` | A whole thread from every folder, oldest first |

### `docgen`

Our own [docgen-mcp](images/docgen-mcp): Markdown to DOCX, PDF and PPTX, spreadsheets, templates and
conversions; the converter behind `convert` is `doc-convert` (Gotenberg with LibreOffice and Chromium
without a network). Files are paths of the space of the user.

| Method | What it does |
| --- | --- |
| `render_document` | A DOCX, PDF or PPTX from Markdown (`source` — a file of the space, or `markdown` — the text): options block, tables, footnotes, images, page breaks, a reference file for styles, speaker notes in PPTX |
| `render_spreadsheet` | An XLSX workbook from a description: sheets, columns with types and formats, rows (a value starting with `=` is a formula), freeze, autofilter, merges, charts, a template to write into |
| `fill_template` | A DOCX, XLSX or PPTX template of the space filled with `{{ field }}` and `{%tr for … %}` loops over table rows |
| `edit_document` | An edit of a file: DOCX and PPTX `replace_text` keeping formatting; XLSX `set_cells`, `append_rows`, `add_sheet` |
| `convert` | Office documents to PDF, DOC/XLS/PPT to DOCX/XLSX/PPTX, an HTML page or a ZIP with `index.html` to PDF; `options`: paper, landscape, margins, `printBackground`, `waitForReady` |
| `pdf_tools` | PDFs of the space: `merge`, `split` (pages like `1-3,5` into a ZIP), `rotate`, `extract` |

### `jira` and `confluence`

Upstream [mcp-atlassian](https://github.com/sooperset/mcp-atlassian) 0.24.6 in read-only mode: the image
takes the token of the user from the request and hides the tools that write. Jira reads issues, projects,
fields, boards, sprints, service desk and work logs; Confluence reads pages, comments, attachments,
labels, spaces and permissions.

<details><summary>Jira: 37 methods</summary>

jira_download_attachments, jira_get_agile_boards, jira_get_all_projects, jira_get_board_issues, jira_get_create_fields, jira_get_cross_project_dependencies, jira_get_field_options, jira_get_issue, jira_get_issue_dates, jira_get_issue_development_info, jira_get_issue_images, jira_get_issue_proforma_forms, jira_get_issue_sla, jira_get_issue_watchers, jira_get_issues_development_info, jira_get_link_types, jira_get_proforma_form_details, jira_get_project_components, jira_get_project_epic_hierarchy, jira_get_project_fields, jira_get_project_issue_types, jira_get_project_issues, jira_get_project_versions, jira_get_queue_issues, jira_get_request_type_fields, jira_get_request_types, jira_get_service_desk_for_project, jira_get_service_desk_queues, jira_get_sprint_issues, jira_get_sprints_from_board, jira_get_transitions, jira_get_user_profile, jira_get_worklog, jira_search, jira_search_assignable_users, jira_search_fields, jira_search_projects
</details>

<details><summary>Confluence: 20 methods</summary>

confluence_check_content_permissions, confluence_download_attachment, confluence_download_content_attachments, confluence_get_attachments, confluence_get_comments, confluence_get_inline_comments, confluence_get_labels, confluence_get_page, confluence_get_page_children, confluence_get_page_diff, confluence_get_page_history, confluence_get_page_images, confluence_get_page_restrictions, confluence_get_page_template, confluence_get_page_views, confluence_get_space_page_tree, confluence_get_space_permissions, confluence_list_page_templates, confluence_search, confluence_search_user
</details>

### `gitlab`

Upstream [@zereight/mcp-gitlab](https://github.com/zereight/gitlab-mcp) 2.3.29 with `REMOTE_AUTHORIZATION`:
the personal token comes with every call, read-only (`read_api`). Projects, groups and members; branches,
commits, files and blame; merge requests with diffs, discussions, notes and approvals; issues; pipelines,
jobs and artifacts; environments and deployments; labels, iterations, todos and search; CI lint.

<details><summary>GitLab: 95 methods</summary>

discover_tools, download_job_artifacts, get_branch, get_branch_diffs, get_ci_catalog_resource, get_commit, get_commit_diff, get_deployment, get_draft_note, get_environment, get_file_blame, get_file_contents, get_group_iteration, get_issue, get_issue_link, get_job_artifact_file, get_merge_request, get_merge_request_approval_state, get_merge_request_conflicts, get_merge_request_diffs, get_merge_request_discussion, get_merge_request_file_diff, get_merge_request_note, get_merge_request_notes, get_merge_request_version, get_namespace, get_pipeline, get_pipeline_job, get_pipeline_job_output, get_pipeline_schedule, get_pipeline_schedule_variable, get_pipeline_test_report, get_pipeline_test_report_summary, get_pipeline_trigger, get_pipeline_variables, get_project, get_protected_branch, get_repository_tree, get_runner, health_check, help, list_branches, list_ci_catalog_resources, list_commit_statuses, list_commits, list_deployment_merge_requests, list_deployments, list_draft_notes, list_environments, list_group_iteration_cadences, list_group_iterations, list_group_members, list_group_merge_requests, list_group_projects, list_issue_discussions, list_issue_emoji_reactions, list_issue_label_events, list_issue_links, list_issue_note_emoji_reactions, list_issue_state_events, list_issues, list_job_artifacts, list_labels, list_merge_request_changed_files, list_merge_request_diffs, list_merge_request_emoji_reactions, list_merge_request_note_emoji_reactions, list_merge_request_pipelines, list_merge_request_versions, list_merge_requests, list_namespaces, list_pipeline_jobs, list_pipeline_schedule_pipelines, list_pipeline_schedules, list_pipeline_trigger_jobs, list_pipeline_triggers, list_pipelines, list_project_iterations, list_project_members, list_project_runners, list_projects, list_protected_branches, list_todos, mr_discussions, my_issues, search_code, search_group_code, search_project_code, search_repositories, validate_ci_lint, validate_project_ci_lint, verify_namespace, wait_for_job, wait_for_pipeline, whoami
</details>

### `clickhouse`

Upstream [mcp-clickhouse](https://github.com/ClickHouse/mcp-clickhouse) 0.7.0 with our middleware: the
personal token (`login:password`) becomes the credentials of the query, and every query goes with
`readonly=1` and the limits of the chart — even a user with the right to write cannot change data.

| Method | Arguments | What it does |
| --- | --- | --- |
| `list_databases` | — | The databases the user may read |
| `list_tables` | `database` | The tables of a database with their columns |
| `run_query` | `query` | Runs one read-only `SELECT` |

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
