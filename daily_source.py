#!/usr/bin/env python3
"""Strict daily arXiv source: keep only first-time (announce_type=new) announcements."""

from __future__ import annotations

import re
import sys
import time
from typing import Any

import feedparser

from fetch_and_mail import (
    CATEGORIES,
    RSS_BASE,
    USER_AGENT,
    Paper,
    clean_text,
    extract_authors,
    fetch_api_category,
    merge_paper,
    normalize_arxiv_link,
)


def get_announce_type(entry: Any) -> str:
    """Read arXiv announce type from namespaced field or RSS/Atom summary text."""
    direct = str(entry.get("arxiv_announce_type", "") or "").strip().lower()
    if direct:
        return direct

    raw = " ".join(
        str(entry.get(key, "") or "")
        for key in ("summary", "description")
    )
    match = re.search(r"Announce\s+Type\s*:\s*([a-z-]+)", raw, flags=re.IGNORECASE)
    return match.group(1).lower() if match else ""


def extract_abstract(entry: Any) -> str:
    raw = str(entry.get("summary", "") or entry.get("description", "") or "")
    # RSS summaries usually start with: arXiv:... Announce Type: ... Abstract: ...
    match = re.search(r"\bAbstract\s*:\s*(.*)$", raw, flags=re.IGNORECASE | re.DOTALL)
    return clean_text(match.group(1) if match else raw)


def entry_categories(entry: Any, requested: str) -> set[str]:
    categories = {requested}
    for tag in entry.get("tags", []) or []:
        term = tag.get("term", "") if isinstance(tag, dict) else getattr(tag, "term", "")
        if term in CATEGORIES:
            categories.add(term)
    for category in entry.get("categories", []) or []:
        if isinstance(category, str) and category in CATEGORIES:
            categories.add(category)
    return categories


def fetch_strict_rss_category(category: str, by_id: dict[str, Paper]) -> tuple[int, int, int]:
    url = RSS_BASE.format(category)
    print(f"Fetching strict-new RSS {url}")
    feed = feedparser.parse(url, request_headers={"User-Agent": USER_AGENT})
    if getattr(feed, "bozo", False):
        print(f"Warning: RSS parser issue for {category}: {feed.bozo_exception}", file=sys.stderr)

    accepted = 0
    rejected_non_new = 0
    missing_type = 0

    for entry in feed.entries:
        announce_type = get_announce_type(entry)
        if announce_type != "new":
            if announce_type:
                rejected_non_new += 1
            else:
                missing_type += 1
            continue

        arxiv_id, link = normalize_arxiv_link(entry.get("link", "") or entry.get("id", ""))
        if not arxiv_id:
            continue

        merge_paper(
            by_id,
            Paper(
                arxiv_id=arxiv_id,
                title=clean_text(entry.get("title", "")),
                authors=extract_authors(entry),
                link=link,
                abstract=extract_abstract(entry),
                categories=entry_categories(entry, category),
            ),
        )
        accepted += 1

    print(
        f"RSS {category}: new={accepted}, non_new_skipped={rejected_non_new}, "
        f"missing_type_skipped={missing_type}"
    )
    return accepted, rejected_non_new, missing_type


def fetch_papers() -> list[Paper]:
    """Fetch only first-time announcements. API fallback is used only when RSS yields no usable entries."""
    by_id: dict[str, Paper] = {}
    for category in CATEGORIES:
        accepted, _non_new, missing = fetch_strict_rss_category(category, by_id)

        # If the feed is unexpectedly empty/unparseable, use the existing API fallback,
        # which targets the latest already-announced submission batch.
        if accepted == 0 and missing > 0:
            try:
                print(f"No parseable new RSS entries for {category}; trying API fallback.")
                fetch_api_category(category, by_id, max_results=1000)
            except Exception as exc:
                print(f"Warning: API fallback failed for {category}: {exc}", file=sys.stderr)
        elif accepted == 0 and not getattr(feedparser.parse(RSS_BASE.format(category)), "entries", []):
            try:
                print(f"RSS truly empty for {category}; trying API fallback.")
                fetch_api_category(category, by_id, max_results=1000)
            except Exception as exc:
                print(f"Warning: API fallback failed for {category}: {exc}", file=sys.stderr)

        time.sleep(1)

    return list(by_id.values())
