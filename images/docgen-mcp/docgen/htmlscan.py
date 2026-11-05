"""Внешние адреса страницы для предупреждений агенту (FTR.NAB.CHAT-0003 R8,
tech §2.3). Блокирует их Chromium без сети; здесь — только понятный ответ."""

from __future__ import annotations

import re

import tinycss2
from lxml import html as lxml_html

EXTERNAL = re.compile(r"^\s*(https?:|ftp:|wss?:|file:|//)", re.I)
IN_SCRIPT = re.compile(r"""["'`]((?:https?|wss?|ftp)://[^"'`\s]+)""", re.I)


def external(markup: bytes, limit: int = 20) -> list[str]:
    found: list[str] = []

    def add(u: str):
        u = u.strip()
        if u and EXTERNAL.match(u) and u not in found:
            found.append(u)

    try:
        tree = lxml_html.fromstring(markup)
    except Exception:
        return found
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in ("src", "href", "poster", "data", "action", "background"):
            if el.get(attr):
                add(el.get(attr))
        if el.get("srcset"):
            for part in el.get("srcset").split(","):
                add(part.strip().split(" ")[0])
        if el.tag == "meta" and (el.get("http-equiv") or "").lower() == "refresh":
            content = el.get("content") or ""
            if "url=" in content.lower():
                add(content.split("=", 1)[1].split("url=", 1)[-1].strip("'\" "))
        if el.tag == "style" and el.text:
            for u in css_urls(el.text):
                add(u)
        if el.get("style"):
            for u in css_urls(el.get("style")):
                add(u)
        if el.tag == "script" and el.text:
            for m in IN_SCRIPT.finditer(el.text):
                add(m.group(1))
    return found[:limit]


def css_urls(css: str) -> list[str]:
    out = []
    for tok in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        stack = [tok]
        while stack:
            t = stack.pop()
            if t.type == "url":
                out.append(t.value)
            elif t.type == "function" and t.lower_name == "url":
                out += [a.value for a in t.arguments if a.type == "string"]
            elif t.type == "at-rule" and t.lower_at_keyword == "import":
                out += [a.value for a in t.prelude if a.type in ("string", "url")]
            for attr in ("content", "prelude", "arguments"):
                stack += [x for x in (getattr(t, attr, None) or []) if hasattr(x, "type")]
    return out
