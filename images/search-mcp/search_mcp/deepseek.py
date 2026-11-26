"""The DeepSeek Search and Fetch APIs as two MCP tools (FTR.INFRA.MCPS-0003 tech §2.2–2.5).

Search returns ranked results with a snippet for a person and a summary for a model; Fetch
returns one public page as Markdown. The vendor is a paid external system: its refusals become
the text of a tool, and no text, log line or header carries the key. `ori_question` and
`localization` are not sent (product, «Не входит»).
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx

from . import config

SEARCH_PATH = "/v1/tools/search"
FETCH_PATH = "/v1/tools/fetch"
RETRY_STATUSES = frozenset({429, 500, 503, 504})
# Field of a result, in the order of the answer: the vendor fields the agent needs.
RESULT_FIELDS = ("title", "url", "snippet", "summary", "date", "site_name", "rerank_score", "authority")


class Refusal(Exception):
    """A refusal of a tool: the text goes to the agent, `result` to the metric (tech §2.5)."""

    def __init__(self, message: str, result: str = "error") -> None:
        super().__init__(message)
        self.result = result


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Cut a body to `limit` bytes without breaking UTF-8; the flag tells the agent it is a part."""
    if not text:
        return "", False
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", errors="ignore"), True


def _message(status: int, detail: str) -> str:
    suffix = f": {detail}" if detail else ""
    templates = {
        400: f"the query was rejected by the search vendor{suffix}; shorten or fix the query",
        401: "the search key was not accepted: ask the operator of the stand",
        402: "the balance of the search account ran out: ask the operator of the stand",
        404: "the page is unavailable: internal addresses and closed pages are not readable",
        429: "the search rate limit is exhausted: retry later",
    }
    return templates.get(status, "the search is unavailable: retry later")


def _detail(body: dict) -> str:
    error = body.get("error")
    value = error.get("message") if isinstance(error, dict) else error
    return str(value or body.get("message") or "").strip()


def _request(cfg: config.Config, path: str, body: dict) -> dict:
    """One call to the vendor with the retries of the chart; the key travels in the header only."""
    if not cfg.vendor_key:
        raise Refusal("the search is not configured: MCP_SEARCH_KEY is not set", "bad_request")
    if not config.url_allowed(cfg.url):
        raise Refusal("the search is not configured: MCP_SEARCH_URL must start with https://", "bad_request")
    url = cfg.url + path
    headers = {"Authorization": f"Bearer {cfg.vendor_key}", "Content-Type": "application/json"}
    attempt = 0
    with httpx.Client(timeout=cfg.timeout) as client:
        while True:
            try:
                response = client.post(url, json=body, headers=headers)
            except httpx.HTTPError:
                raise Refusal("the search is unavailable: retry later", "vendor_error") from None
            status = response.status_code
            try:
                decoded = response.json() if response.content else {}
            except ValueError:
                decoded = {}
            answer = decoded if isinstance(decoded, dict) else {}
            if status == 200:
                return answer
            if status in RETRY_STATUSES and attempt < cfg.retries:
                attempt += 1
                time.sleep(cfg.retry_delay * attempt)
                continue
            label = {400: "bad_request", 404: "error"}.get(status, "vendor_error")
            raise Refusal(_message(status, _detail(answer)), label)


def search(cfg: config.Config, query: str, count: int, need_content: bool) -> dict:
    """Search the web; the query keeps the operators of the vendor (`site:`, `after:`, `lang:`)."""
    query = (query or "").strip()
    if not query:
        raise Refusal("the query is empty: send 1 to 128 characters", "bad_request")
    if len(query) > config.MAX_QUERY:
        raise Refusal(f"the query is too long: {config.MAX_QUERY} characters at most, shorten it", "bad_request")
    if count < 1 or count > config.MAX_COUNT:
        raise Refusal(f"count must be between 1 and {config.MAX_COUNT}", "bad_request")
    page = _request(cfg, SEARCH_PATH, {"query": query, "count": count, "need_content": need_content})
    results = []
    truncated = False
    for item in page.get("results") or []:
        if not isinstance(item, dict):
            continue
        result = {field: item.get(field) for field in RESULT_FIELDS}
        content, cut = _truncate(str(item.get("content") or ""), cfg.max_content_bytes)
        result["content"] = content
        truncated = truncated or cut
        results.append(result)
    return {"query": query, "count": len(results), "results": results, "truncated": truncated}


def fetch(cfg: config.Config, url: str) -> dict:
    """Read one public page as Markdown; internal addresses never reach the vendor (R7)."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise Refusal("url must be an http(s) address with a host and without a login", "bad_request")
    if len(url) > 8192:
        raise Refusal("url is too long: 8192 characters at most", "bad_request")
    page = _request(cfg, FETCH_PATH, {"url": url})
    content, truncated = _truncate(str(page.get("content") or ""), cfg.max_content_bytes)
    return {"url": url, "title": str(page.get("title") or ""), "content": content, "truncated": truncated}
