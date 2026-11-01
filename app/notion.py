"""Small Notion API client (standard library only) and property helpers."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"  # uses data source ids (collection://...) for create and query
TEXT_LIMIT = 2000       # Notion's max characters per rich-text piece


class NotionError(Exception):
    pass


class Notion:
    def __init__(self, token: str, timeout: int = 20):
        self.token = token
        self.timeout = timeout

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            f"{API}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Notion-Version": VERSION,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read()).get("message", "")
            except ValueError:
                message = ""
            raise NotionError(f"{e.code}: {message or e.reason}") from None
        except OSError as e:
            raise NotionError(f"can't reach Notion ({e})") from None

    def create_page(self, data_source: str, properties: dict,
                    children: list[dict] | None = None) -> str:
        body = {"parent": {"type": "data_source_id", "data_source_id": data_source},
                "properties": properties}
        if children:
            body["children"] = children[:100]
        return self._call("POST", "/pages", body)["id"]

    def query(self, data_source: str, filter: dict | None = None,
              sorts: list[dict] | None = None, limit: int = 200) -> list[dict]:
        results, cursor = [], None
        while len(results) < limit:
            body: dict = {"page_size": min(100, limit - len(results))}
            if filter:
                body["filter"] = filter
            if sorts:
                body["sorts"] = sorts
            if cursor:
                body["start_cursor"] = cursor
            page = self._call("POST", f"/data_sources/{data_source}/query", body)
            results.extend(page.get("results", []))
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
        return results

    def get_page(self, page_id: str) -> dict:
        return self._call("GET", f"/pages/{page_id}")

    def update_page(self, page_id: str, properties: dict | None = None,
                    in_trash: bool | None = None) -> None:
        body: dict = {}
        if properties:
            body["properties"] = properties
        if in_trash is not None:
            body["in_trash"] = in_trash
        self._call("PATCH", f"/pages/{page_id}", body)

    def append_blocks(self, page_id: str, children: list[dict]) -> list[str]:
        """Add blocks to the end of a page; returns the new block ids."""
        ids = []
        for i in range(0, len(children), 100):
            result = self._call("PATCH", f"/blocks/{page_id}/children",
                                {"children": children[i:i + 100]})
            ids += [b["id"] for b in result.get("results", [])]
        return ids

    def delete_block(self, block_id: str) -> None:
        self._call("DELETE", f"/blocks/{block_id}")

    def trash(self, page_id: str) -> None:
        self.update_page(page_id, in_trash=True)

    def check(self, data_source: str) -> str:
        """The data source's title; raises NotionError if the integration can't see it."""
        info = self._call("GET", f"/data_sources/{data_source}")
        return "".join(t.get("plain_text", "") for t in info.get("title", [])) or "(untitled)"


# --- property builders -----------------------------------------------------------

def _rich(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text[i:i + TEXT_LIMIT]}}
            for i in range(0, len(text), TEXT_LIMIT)] or []


def title(text: str) -> dict:
    return {"title": _rich(text)}


def rich_text(text: str) -> dict:
    return {"rich_text": _rich(text)}


def select(name: str | None) -> dict:
    return {"select": {"name": name} if name else None}


def multi_select(names: list[str]) -> dict:
    return {"multi_select": [{"name": n[:100]} for n in names]}


def date_prop(d: date) -> dict:
    return {"date": {"start": d.isoformat()}}


def number(n: float | None) -> dict:
    return {"number": n}


def checkbox(value: bool) -> dict:
    return {"checkbox": value}


def relation(page_ids: list[str]) -> dict:
    return {"relation": [{"id": i} for i in page_ids]}


def heading(text: str, level: int = 3) -> dict:
    kind = f"heading_{level}"
    return {"object": "block", "type": kind, kind: {"rich_text": _rich(text)}}


def paragraphs(text: str) -> list[dict]:
    """Page body: one paragraph block per line (blank lines kept as spacing)."""
    blocks = []
    for line in text.splitlines() or [text]:
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text": _rich(line)}})
    return blocks


# --- property readers --------------------------------------------------------------

def plain(page: dict, name: str) -> str:
    prop = page.get("properties", {}).get(name) or {}
    parts = prop.get(prop.get("type", ""), []) or []
    return "".join(p.get("plain_text", "") for p in parts) if isinstance(parts, list) else ""


def get_select(page: dict, name: str) -> str:
    prop = page.get("properties", {}).get(name) or {}
    return ((prop.get("select") or {}).get("name")) or ""


def get_number(page: dict, name: str) -> float | None:
    return (page.get("properties", {}).get(name) or {}).get("number")


def get_date(page: dict, name: str) -> date | None:
    start = ((page.get("properties", {}).get(name) or {}).get("date") or {}).get("start")
    return date.fromisoformat(start[:10]) if start else None
