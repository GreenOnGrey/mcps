"""Limits and addresses of the service (FTR.NAB.CHAT-0003 R12, tech §2.1)."""

import json
import os

from .errors import DocError


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


MB = 1 << 20

KEY = os.environ.get("DOCGEN_KEY", "")
S3_HOSTS = {h.strip() for h in os.environ.get("DOCGEN_S3_HOSTS", "s3.storages.svc:9000").split(",") if h.strip()}
CONVERT_URL = os.environ.get("DOC_CONVERT_URL", "http://doc-convert:3000").rstrip("/")
CONVERT_USER = os.environ.get("DOC_CONVERT_USER", "")
CONVERT_PASSWORD = os.environ.get("DOC_CONVERT_PASSWORD", "")
LEGACY_URL = os.environ.get("DOC_CONVERT_LEGACY_URL", "http://doc-convert:3001").rstrip("/")

MAX_INLINE = _int("DOCGEN_MAX_INLINE_MB", 4) * MB
MAX_SOURCE = _int("DOCGEN_MAX_SOURCE_MB", 25) * MB
MAX_RESULT = _int("DOCGEN_MAX_RESULT_MB", 50) * MB
TIMEOUT = _int("DOCGEN_TIMEOUT_SECONDS", 120)
MAX_PAGES = _int("DOCGEN_MAX_PAGES", 500)
ZIP_MAX = _int("DOCGEN_ZIP_MAX_MB", 100) * MB
ZIP_MAX_FILES = _int("DOCGEN_ZIP_MAX_FILES", 1000)
WORK_DIR = os.environ.get("DOCGEN_WORK_DIR", "/tmp/docgen")
PANDOC = os.environ.get("PANDOC", "pandoc")


def check_inline(name: str, value) -> None:
    """Markdown, the JSON of a table and template data in arguments: at most MAX_INLINE (R12)."""
    size = len(value.encode()) if isinstance(value, str) else len(json.dumps(value, ensure_ascii=False, default=str).encode())
    if size > MAX_INLINE:
        raise DocError(f"{name} is larger than {MAX_INLINE // MB} MB: put the data into a file of the space.")
