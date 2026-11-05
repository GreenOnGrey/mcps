"""Downloading sources by links of the Nabu proxy and uploading the result
(FTR.NAB.CHAT-0003 tech §2.2): only hosts of S3 of the stand, streamed and with a limit.
The path of a file in the workspace comes in the fragment #nabu-path=…"""

from __future__ import annotations

import os
import posixpath
import urllib.error
import urllib.parse
import urllib.request

from . import limits
from .errors import DocError


def split(url: str) -> tuple[str, str]:
    """A link without the fragment and the path of a file in the workspace (or the name from the link)."""
    base, _, frag = url.partition("#")
    rel = ""
    if frag.startswith("nabu-path="):
        rel = urllib.parse.unquote(frag[len("nabu-path="):])
    rel = safe_rel(rel) or safe_rel(posixpath.basename(urllib.parse.urlparse(base).path)) or "file"
    return base, rel


def safe_rel(p: str) -> str:
    """A relative path without .. and without a root; otherwise an empty string."""
    p = p.replace("\\", "/").strip()
    norm = posixpath.normpath(p) if p else ""
    if not norm or norm in (".", "..") or norm.startswith("/") or norm.startswith("../"):
        return ""
    return norm


def check_host(url: str) -> str:
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or u.netloc not in limits.S3_HOSTS:
        raise DocError("A file argument must be a link given by Nabu; other addresses are not fetched.")
    return url


def download(url: str, workdir: str, max_bytes: int | None = None) -> str:
    """Downloads a file by a proxy link to workdir at its path in the workspace."""
    max_bytes = max_bytes or limits.MAX_SOURCE
    base, rel = split(url)
    check_host(base)
    dest = os.path.join(workdir, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with urllib.request.urlopen(base, timeout=60) as resp:
            length = resp.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise DocError(f"The file {rel} is larger than {max_bytes // limits.MB} MB.")
            n = 0
            with open(dest, "wb") as f:
                while chunk := resp.read(1 << 16):
                    n += len(chunk)
                    if n > max_bytes:
                        raise DocError(f"The file {rel} is larger than {max_bytes // limits.MB} MB.")
                    f.write(chunk)
    except urllib.error.URLError as e:
        raise DocError(f"The file {rel} could not be read: {e.reason}") from None
    return dest


def upload(put_url: str, path: str) -> int:
    """Uploads the result with one PUT by a proxy link."""
    check_host(put_url.partition("#")[0])
    size = os.path.getsize(path)
    if size > limits.MAX_RESULT:
        raise DocError(f"The result is larger than {limits.MAX_RESULT // limits.MB} MB.")
    with open(path, "rb") as f:
        req = urllib.request.Request(put_url, data=f.read(), method="PUT", headers={"Content-Length": str(size)})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            if resp.status // 100 != 2:
                raise DocError(f"The result could not be saved: HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        raise DocError(f"The result could not be saved: HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise DocError(f"The result could not be saved: {e.reason}") from None
    return size
