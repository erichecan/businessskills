"""Collect public business material into a pipeline-private evidence store.

The collector intentionally uses public Google News RSS discovery followed by
ordinary article-page requests. It does not log into Xiaohongshu, scrape social
feeds, or accept manual directory imports.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .runtime import active_context

USER_AGENT = "PoStoryPublicSourceCollector/1.0 (+https://postory.ca; public article indexing)"
MAX_PAGE_BYTES = 3_000_000
MIN_BODY_CHARS = 250


def _clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"[\t\r\f\v ]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    return value.strip()


def _normal_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value.strip())
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if not k.lower().startswith("utm_")
             and k.lower() not in {"fbclid", "gclid", "ref", "source"}]
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                                    parts.path.rstrip("/"), urllib.parse.urlencode(query), ""))


class _PageParser(HTMLParser):
    """Small dependency-free article extractor with preference for article/main."""

    IGNORE = {"script", "style", "noscript", "svg", "iframe", "nav", "footer", "header", "form"}
    MAIN = {"article", "main"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.all_parts: list[str] = []
        self.main_parts: list[str] = []
        self.meta_description = ""
        self.image_url = ""
        self.canonical = ""
        self.stack: list[tuple[str, bool, bool]] = []
        self.in_title = False
        self.ignored = 0
        self.main_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            name = (attrs.get("name") or attrs.get("property") or "").lower()
            if name in {"description", "og:description", "twitter:description"} and not self.meta_description:
                self.meta_description = attrs.get("content", "")
            if name in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"} and not self.image_url:
                self.image_url = attrs.get("content", "")
        if tag == "link" and "canonical" in (attrs.get("rel") or "").lower():
            self.canonical = attrs.get("href", "")
        if tag == "title":
            self.in_title = True
        was_ignored = self.ignored > 0 or tag in self.IGNORE
        is_main = self.main_depth > 0 or tag in self.MAIN
        self.stack.append((tag, was_ignored, is_main))
        if tag in self.IGNORE:
            self.ignored += 1
        if tag in self.MAIN:
            self.main_depth += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        for index in range(len(self.stack) - 1, -1, -1):
            stack_tag, was_ignored, is_main = self.stack[index]
            if stack_tag == tag:
                removed = self.stack[index:]
                del self.stack[index:]
                self.ignored = sum(1 for t, _, _ in self.stack if t in self.IGNORE)
                self.main_depth = sum(1 for t, _, _ in self.stack if t in self.MAIN)
                break

    def handle_data(self, data):
        text = _clean_text(data)
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        if self.ignored:
            return
        self.all_parts.append(text)
        if self.main_depth:
            self.main_parts.append(text)

    def result(self) -> dict[str, str]:
        body_parts = self.main_parts if len(" ".join(self.main_parts)) >= MIN_BODY_CHARS else self.all_parts
        return {
            "title": _clean_text(" ".join(self.title_parts)),
            "description": _clean_text(self.meta_description),
            "image_url": self.image_url,
            "canonical": self.canonical,
            "body": _clean_text("\n".join(body_parts)),
        }


def _request(url: str, *, accept: str, timeout: int = 25) -> tuple[bytes, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(MAX_PAGE_BYTES + 1)
        if len(payload) > MAX_PAGE_BYTES:
            payload = payload[:MAX_PAGE_BYTES]
        content_type = response.headers.get("Content-Type", "")
        final_url = response.geturl()
    charset_match = re.search(r"charset=([^; ]+)", content_type, re.I)
    charset = charset_match.group(1).strip('"\'') if charset_match else "utf-8"
    return payload, final_url, charset


def _feed_url(query: str, language: str, region: str) -> str:
    ceid_language = language.split("-", 1)[0] if language.startswith("en-") else language
    params = urllib.parse.urlencode({"q": query, "hl": language, "gl": region,
                                    "ceid": f"{region}:{ceid_language}"})
    return f"https://news.google.com/rss/search?{params}"


def _read_feed(source: dict[str, Any]) -> list[dict[str, str]]:
    settings = source["settings"]
    query = settings["query"]
    published_within_days = settings.get("published_within_days")
    if published_within_days and not re.search(r"\b(?:when:|after:|before:)\S+", query):
        if published_within_days % 365 == 0:
            window = f"{published_within_days // 365}y"
        elif published_within_days % 30 == 0:
            window = f"{published_within_days // 30}m"
        else:
            window = f"{published_within_days}d"
        query = f"{query} when:{window}"
    feed_url = settings.get("feed_url") or _feed_url(
        query, settings.get("language", "zh-CN"), settings.get("region", "CN"))
    raw, _, charset = _request(feed_url, accept="application/rss+xml, application/xml, text/xml")
    root = ET.fromstring(raw.decode(charset, errors="replace"))
    entries = []
    for item in root.findall(".//item"):
        title = _clean_text(item.findtext("title", ""))
        link = _normal_url(item.findtext("link", ""))
        if not title or not link:
            continue
        pub_date = _clean_text(item.findtext("pubDate", ""))
        description = _clean_text(re.sub(r"<[^>]+>", " ", item.findtext("description", "")))
        source_name = ""
        for child in item:
            if child.tag.endswith("source"):
                source_name = _clean_text(child.text or "")
                break
        entries.append({"title": title, "url": link, "published_at": pub_date,
                        "description": description, "publisher": source_name})
    return entries


def _extract_page(url: str) -> dict[str, str]:
    payload, final_url, charset = _request(url, accept="text/html,application/xhtml+xml")
    parser = _PageParser()
    parser.feed(payload.decode(charset, errors="replace"))
    result = parser.result()
    canonical = urllib.parse.urljoin(final_url, result.pop("canonical", "")) if result.get("canonical") else ""
    image_url = urllib.parse.urljoin(final_url, result.get("image_url", "")) if result.get("image_url") else ""
    result["canonical_url"] = _normal_url(canonical or final_url)
    result["image_url"] = _normal_url(image_url) if image_url else ""
    result["resolved_url"] = _normal_url(final_url)
    return result


def _resolve_google_news_url(url: str) -> str:
    """Resolve Google's RSS wrapper through its current public article RPC.

    Google News RSS URLs no longer reliably redirect to the publisher. The RPC
    is undocumented and may change; on failure the caller records a source
    item error instead of treating the Google shell page as source material.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.hostname not in {"news.google.com", "www.news.google.com"} or "/articles/" not in parts.path:
        return url
    article_id = parts.path.rsplit("/", 1)[-1]
    page_url = urllib.parse.urlunsplit(("https", "news.google.com", f"/rss/articles/{article_id}", parts.query, ""))
    page, _, charset = _request(page_url, accept="text/html,application/xhtml+xml")
    page_html = page.decode(charset, errors="replace")
    attrs = {
        key: value for key, value in re.findall(
            r'(data-n-a-(?:id|ts|sg))="([^"]+)"', page_html
        )
    }
    article_id = attrs.get("data-n-a-id", article_id)
    timestamp = attrs.get("data-n-a-ts", "")
    signature = attrs.get("data-n-a-sg", "")
    if not timestamp.isdigit() or not signature:
        raise ValueError("Google News 未提供文章解析参数（可能遇到限流或页面结构变化）")

    locale = "US:en"
    query = dict(urllib.parse.parse_qsl(parts.query))
    if query.get("ceid"):
        locale = query["ceid"]
    inner = [
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, locale, None, 1,
          None, None, None, None, None, 0, 1],
         "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        article_id, int(timestamp), signature,
    ]
    outer = [[["Fbv4je", json.dumps(inner, ensure_ascii=False, separators=(",", ":"))]]]
    body = urllib.parse.urlencode({"f.req": json.dumps(outer, separators=(",", ":"))}).encode()
    request = urllib.request.Request(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://news.google.com",
            "Referer": "https://news.google.com/",
            "X-Same-Domain": "1",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        result = response.read(500_000).decode("utf-8", errors="replace")
    try:
        frames = json.loads(result.split("\n\n", 1)[1])
        frame = next(row for row in frames if isinstance(row, list) and row[:2] == ["wrb.fr", "Fbv4je"])
        resolved = json.loads(frame[2])[1]
    except (IndexError, KeyError, StopIteration, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Google News 未能解析出原文链接") from exc
    parsed = urllib.parse.urlsplit(resolved)
    if parsed.scheme not in {"http", "https"} or parsed.hostname in {"news.google.com", "www.google.com"}:
        raise ValueError("Google News 返回的仍是包装链接")
    return _normal_url(resolved)


def _content_hash(title: str, body: str) -> str:
    material = re.sub(r"\W+", "", f"{title}\n{body}".lower())
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _matrix_matches(text: str, matrix: dict[str, Any]) -> list[str]:
    lowered = text.lower()
    matches = []
    for topic in matrix.get("topics", []):
        topic_id = topic.get("id", "")
        terms = topic.get("terms", [])
        if topic_id and any(str(term).lower() in lowered for term in terms if term):
            matches.append(topic_id)
    return matches


def _freshness(published_at: str) -> tuple[str, int | None]:
    try:
        published = parsedate_to_datetime(published_at)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = max(0, (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).days)
    except (TypeError, ValueError, OverflowError):
        return "unknown", None
    return ("recent" if age_days <= 365 else "evergreen_or_archive"), age_days


def _seen_urls(items_dir: Path) -> set[str]:
    found: set[str] = set()
    if not items_dir.exists():
        return found
    for path in items_dir.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("canonical_url"):
                found.add(record["canonical_url"])
            if record.get("discovered_url"):
                found.add(record["discovered_url"])
        except (OSError, json.JSONDecodeError):
            continue
    return found


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)


def _append_topic_queue(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(json.loads(line).get("content_hash"))
            except json.JSONDecodeError:
                continue
    if record["content_hash"] in existing:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def collect(*, dry_run: bool = False, source_ids: set[str] | None = None) -> dict[str, Any]:
    context = active_context(required=True)
    collection = context.collection
    sources = [source for source in collection["sources"]
               if source.get("enabled") and (source_ids is None or source["id"] in source_ids)]
    if not sources:
        raise ValueError("没有启用的公开来源")
    root = context.data_root / "collection"
    items_dir = root / "items"
    seen_urls = _seen_urls(items_dir)
    seen_hashes = set()
    if items_dir.exists():
        for path in items_dir.glob("*.json"):
            try:
                seen_hashes.add(json.loads(path.read_text(encoding="utf-8")).get("content_hash"))
            except (OSError, json.JSONDecodeError):
                pass

    results = {"pipeline_id": context.pipeline_id, "started_at": datetime.now(timezone.utc).isoformat(),
               "sources": [], "discovered": 0, "saved": 0, "usable": 0,
               "image_available": 0, "image_missing": 0, "duplicates": 0, "errors": 0}
    matrix = collection.get("editorial_matrix", {})
    state_path = root / "source_state.json"
    try:
        states = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        states = {}

    for source in sources:
        entry = {"source_id": source["id"], "discovered": 0, "saved": 0,
                 "usable": 0, "image_available": 0, "image_missing": 0,
                 "duplicates": 0, "errors": []}
        try:
            feed_items = _read_feed(source)
            cap = source["settings"].get("max_items", 10)
            # Apply the per-run limit after skipping previously stored URLs.
            # Otherwise the same top RSS results can occupy the cap forever,
            # preventing newer candidates later in the feed from being seen.
            discovered = []
            excluded_title_terms = [
                str(term).lower() for term in source.get("settings", {}).get("exclude_title_terms", [])
            ]
            for candidate in feed_items:
                if candidate["url"] in seen_urls:
                    entry["duplicates"] += 1
                    results["duplicates"] += 1
                    continue
                if any(term in candidate["title"].lower() for term in excluded_title_terms):
                    entry.setdefault("excluded_irrelevant", 0)
                    entry["excluded_irrelevant"] += 1
                    continue
                discovered.append(candidate)
                if len(discovered) >= cap:
                    break
            entry["discovered"] = len(discovered)
            results["discovered"] += len(discovered)
            for item in discovered:
                try:
                    item_url = item["url"]
                    if item_url in seen_urls:
                        entry["duplicates"] += 1
                        results["duplicates"] += 1
                        continue
                    resolved_url = _resolve_google_news_url(item_url)
                    page = _extract_page(resolved_url)
                    canonical_url = page["canonical_url"]
                    body = page["body"]
                    title = page["title"] or item["title"]
                    digest = _content_hash(title, body or item["description"])
                    if canonical_url in seen_urls or digest in seen_hashes:
                        seen_urls.add(canonical_url)
                        entry["duplicates"] += 1
                        results["duplicates"] += 1
                        continue
                    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    # Article body footers/nav text often contain generic terms
                    # such as "business", "service" or "growth". Focus on the
                    # headline, RSS summary and opening section for relevance.
                    matches = _matrix_matches(
                        f"{title}\n{item['description']}\n{body[:500]}", matrix
                    )
                    freshness, age_days = _freshness(item.get("published_at", ""))
                    record = {
                        "schema_version": 1,
                        "pipeline_id": context.pipeline_id,
                        "source_id": source["id"],
                        "source_name": source.get("name", source["id"]),
                        "source_kind": source.get("kind", "public_media"),
                        "source_tags": source.get("tags", []),
                        "discovered_url": item_url,
                        "canonical_url": canonical_url,
                        "publisher": item.get("publisher", ""),
                        "title": title,
                        "published_at": item.get("published_at", ""),
                        "freshness": freshness,
                        "published_age_days": age_days,
                        "collected_at": now,
                        "description": page["description"] or item["description"],
                        "image_url": page.get("image_url") or item.get("image_url", ""),
                        "image_status": "available" if page.get("image_url") or item.get("image_url") else "no_public_image",
                        "body": body,
                        "content_status": "full_text" if len(body) >= MIN_BODY_CHARS else "summary_only",
                        "content_hash": digest,
                        "matrix_matches": matches,
                        "relevance_status": "topic_match" if matches else "needs_review",
                        "usage_note": source.get("usage_note", "仅作为事实与选题来源；内容需综合改写并核对来源。"),
                    }
                    if not dry_run:
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
                        item_path = items_dir / f"{stamp}-{digest[:16]}.json"
                        _atomic_json(item_path, record)
                        if matches and len(body) >= MIN_BODY_CHARS:
                            _append_topic_queue(root / "topic_queue.jsonl", {
                                "content_hash": digest,
                                "source_id": record["source_id"],
                                "title": title,
                                "source_url": canonical_url,
                                "published_at": record["published_at"],
                                "matrix_matches": matches,
                                "status": "unreviewed",
                                "collected_at": now,
                            })
                    seen_urls.add(canonical_url)
                    seen_urls.add(item_url)
                    seen_hashes.add(digest)
                    entry["saved"] += 1
                    results["saved"] += 1
                    if len(body) >= MIN_BODY_CHARS and matches:
                        entry["usable"] += 1
                        results["usable"] += 1
                    if record["image_url"]:
                        entry["image_available"] += 1
                        results["image_available"] += 1
                    else:
                        entry["image_missing"] += 1
                        results["image_missing"] += 1
                    time.sleep(float(collection.get("request_delay_seconds", 1.5)))
                except Exception as exc:  # source item errors are isolated
                    entry["errors"].append(f"{item.get('url', '')}: {type(exc).__name__}: {exc}")
                    results["errors"] += 1
        except Exception as exc:
            entry["errors"].append(f"feed: {type(exc).__name__}: {exc}")
            results["errors"] += 1
        entry["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        results["sources"].append(entry)
        states[source["id"]] = {"last_run_at": entry["completed_at"], "last_error": entry["errors"][-1:] or [],
                                 "last_discovered": entry["discovered"], "last_saved": entry["saved"]}
        if not dry_run:
            _atomic_json(state_path, states)

    results["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not dry_run:
        _atomic_json(root / "last_run.json", results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="从配置的公开来源采集经营与营销素材")
    parser.add_argument("--pipeline", default="postory-xhs")
    parser.add_argument("--source", action="append", dest="source_ids", help="仅运行指定来源 ID，可重复")
    parser.add_argument("--dry-run", action="store_true", help="抓取并统计，不写入素材池")
    args = parser.parse_args()
    import os
    os.environ["XHS_PIPELINE_ID"] = args.pipeline
    try:
        result = collect(dry_run=args.dry_run, source_ids=set(args.source_ids) if args.source_ids else None)
    except Exception as exc:
        print(f"⛔ 公开来源采集失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] and not result["saved"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
