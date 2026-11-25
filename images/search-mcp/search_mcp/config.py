"""Settings of the server (FTR.INFRA.MCPS-0003 tech §4).

The key comes from the Secret mcp-deepseek-key, the address of the API is a value of the chart.
The server starts without either: `/health` stays 200 and a tool names what is missing (R8).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_URL = "https://api.deepseek.com"
MAX_QUERY = 128
MAX_COUNT = 50
DEFAULT_COUNT = 10
# The loopback exception exists for the test of the image: the fake vendor speaks plain HTTP.
LOOPBACK = ("127.0.0.1", "localhost", "[::1]", "::1")


@dataclass(frozen=True)
class Config:
    key: str
    url: str
    timeout: float
    max_content_bytes: int
    retries: int
    retry_delay: float


def load() -> Config:
    """The settings of this call: the environment is read every time, so a test can replace it."""
    return Config(
        key=os.environ.get("SEARCH_KEY", "").strip(),
        url=os.environ.get("SEARCH_URL", DEFAULT_URL).strip().rstrip("/") or DEFAULT_URL,
        timeout=float(os.environ.get("SEARCH_TIMEOUT_SECONDS", "30")),
        max_content_bytes=int(os.environ.get("SEARCH_MAX_CONTENT_BYTES", "102400")),
        retries=int(os.environ.get("SEARCH_RETRIES", "2")),
        retry_delay=float(os.environ.get("SEARCH_RETRY_DELAY", "1")),
    )


def url_allowed(url: str) -> bool:
    """`https://` only; plain `http://` is for the loopback address of the test of the image (R3)."""
    if url.startswith("https://"):
        return True
    if not url.startswith("http://"):
        return False
    rest = url.removeprefix("http://")
    host = rest.split("/", 1)[0].rsplit(":", 1)[0]
    return host in LOOPBACK
