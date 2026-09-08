#!/usr/bin/env python3
"""Fetch latest arXiv papers, filter robotics interests, and email a compact digest."""

from __future__ import annotations

import argparse
import html
import os
import re
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import feedparser
import yaml
from bs4 import BeautifulSoup

CATEGORIES = ["cs.RO", "cs.AI", "cs.LG", "cs.CV", "eess.SY"]
RSS_BASE = "https://rss.arxiv.org/rss/{}"
NEW_LIST_BASE = "https://arxiv.org/list/{}/new?show=2000"
TIMEZONE = ZoneInfo("Asia/Shanghai")
USER_AGENT = "Pluto-arXiv-Daily/1.2 (+https://github.com/Pluto-134/arxiv)"


@dataclass
class Paper:
    arxiv_id: str
    title: str
    authors: str
    link: str
    abstract: str
    categories: set[str] = field(default_factory=set)
    score: int = 0
    gate_reason: str = ""
    matched_groups: list[str] = field(default_factory=list)


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def contains_term(text: str, term: str) -> bool:
    """Phrase match with sensible word boundaries for short tokens such as VLA/UAV."""
    text = text.lower()
    term = term.lower().strip()
    if not term:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(term).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def normalize_arxiv_link(link: str) -> tuple[str, str]:
    link = (link or "").strip().replace("http://", "https://")
    match = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(?:v\d+)?", link, re.I)
    if not match:
        return link, link
    arxiv_id = match.group(1)
    return arxiv_id, f"https://arxiv.org/abs/{arxiv_id}"


def extract_authors(entry: Any) -> str:
    authors = []
    for author in entry.get("authors", []) or []:
        name = clean_text(author.get("name", "") if isinstance(author, dict) else getattr(author, "name", ""))
        if name:
            authors.append(name)
    if authors:
        return ", ".join(authors)

    raw = entry.get("author") or entry.get("dc_creator") or entry.get("creator") or "Unknown authors"
    return clean_text(str(raw))


def merge_paper(by_id: dict[str, Paper], paper: Paper) -> None:
    existing = by_id.get(paper.arxiv_id)
    if existing is None:
        by_id[paper.arxiv_id] = paper
        return

    existing.categories.update(paper.categories)
    if not existing.abstract and paper.abstract:
        existing.abstract = paper.abstract
    if (not existing.authors or existing.authors == "Unknown authors") and paper.authors:
        existing.authors = paper.authors


def fetch_rss_category(category: str, by_id: dict[str, Paper]) -> int:
    """Fetch new/cross-listed entries from one RSS feed. Return accepted entry count."""
    url = RSS_BASE.format(category)
    print(f"Fetching RSS {url}")
    feed = feedparser.parse(url, request_headers={"User-Agent": USER_AGENT})
    if getattr(feed, "bozo", False):
        print(f"Warning: RSS parser issue for {category}: {feed.bozo_exception}", file=sys.stderr)

    accepted = 0
    for entry in feed.entries:
        announce_type = str(entry.get("arxiv_announce_type", "")).lower().strip()
        if announce_type and announce_type not in {"new", "cross", "cross-list"}:
            continue

        raw_link = entry.get("link", "")
        arxiv_id, link = normalize_arxiv_link(raw_link)
        if not arxiv_id:
            continue

        merge_paper(
            by_id,
            Paper(
                arxiv_id=arxiv_id,
                title=clean_text(entry.get("title", "")),
                authors=extract_authors(entry),
                link=link,
                abstract=clean_text(entry.get("summary", "") or entry.get("description", "")),
                categories={category},
            ),
        )
        accepted += 1

    return accepted


def extract_list_categories(subject_text: str) -> set[str]:
    cats = set(re.findall(r"\(([A-Za-z.-]+)\)", subject_text or ""))
    return {cat for cat in cats if cat in CATEGORIES}


def fetch_new_list_category(category: str, by_id: dict[str, Paper]) -> int:
    """Fallback: scrape arXiv /new, keeping New submissions + Cross-lists only."""
    url = NEW_LIST_BASE.format(category)
    print(f"RSS empty for {category}; falling back to {url}")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urlopen(request, timeout=30) as response:
        soup = BeautifulSoup(response.read(), "html.parser")

    accepted = 0
    for heading in soup.find_all("h3"):
        heading_text = clean_text(heading.get_text(" ", strip=True)).lower()
        if not (heading_text.startswith("new submissions") or heading_text.startswith("cross-lists")):
            continue

        listing = heading.find_next_sibling("dl")
        if listing is None:
            continue

        dts = listing.find_all("dt", recursive=False)
        dds = listing.find_all("dd", recursive=False)
        for dt, dd in zip(dts, dds):
            abs_link = dt.find("a", href=re.compile(r"^/abs/"))
            if abs_link is None:
                continue

            href = str(abs_link.get("href", ""))
            arxiv_id = re.sub(r"^/abs/", "", href).split("v", 1)[0]
            if not arxiv_id:
                continue

            title_div = dd.find("div", class_="list-title")
            title = clean_text(title_div.get_text(" ", strip=True) if title_div else "")
            title = re.sub(r"^Title:\s*", "", title, flags=re.IGNORECASE)

            authors_div = dd.find("div", class_="list-authors")
            if authors_div:
                names = [clean_text(a.get_text(" ", strip=True)) for a in authors_div.find_all("a")]
                authors = ", ".join(name for name in names if name)
                if not authors:
                    authors = re.sub(
                        r"^Authors?:\s*", "", clean_text(authors_div.get_text(" ", strip=True)), flags=re.IGNORECASE
                    )
            else:
                authors = "Unknown authors"

            abstract_p = dd.find("p", class_="mathjax")
            abstract = clean_text(abstract_p.get_text(" ", strip=True) if abstract_p else "")

            subjects_div = dd.find("div", class_="list-subjects")
            subjects_text = clean_text(subjects_div.get_text(" ", strip=True) if subjects_div else "")
            categories = {category} | extract_list_categories(subjects_text)

            merge_paper(
                by_id,
                Paper(
                    arxiv_id=arxiv_id,
                    title=title,
                    authors=authors,
                    link=f"https://arxiv.org/abs/{arxiv_id}",
                    abstract=abstract,
                    categories=categories,
                ),
            )
            accepted += 1

    print(f"Fallback {category}: accepted {accepted} new/cross-listed entries")
    return accepted


def fetch_papers() -> list[Paper]:
    by_id: dict[str, Paper] = {}

    for category in CATEGORIES:
        rss_count = fetch_rss_category(category, by_id)
        if rss_count == 0:
            try:
                fetch_new_list_category(category, by_id)
            except Exception as exc:
                print(f"Warning: HTML fallback failed for {category}: {exc}", file=sys.stderr)
        time.sleep(1)

    return list(by_id.values())


def load_rules(path: str = "keywords.yaml") -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def robotics_gate(paper: Paper, rules: dict[str, Any]) -> tuple[bool, str]:
    """High-precision first-stage gate.

    cs.RO papers are allowed to proceed to interest scoring because the category itself
    is a strong robotics prior. Papers from AI/LG/CV/SY must contain an explicit robotics
    or embodied-intelligence anchor; generic words such as navigation/manipulation do not
    count as anchors by themselves.
    """
    full_text = f"{paper.title} {paper.abstract}"

    for term in rules.get("strong_robot_anchors", []):
        if contains_term(full_text, str(term)):
            return True, f"anchor:{term}"

    if "cs.RO" in paper.categories:
        return True, "category:cs.RO"

    return False, "no-explicit-robot-anchor"


def score_paper(paper: Paper, rules: dict[str, Any]) -> int:
    passed, gate_reason = robotics_gate(paper, rules)
    paper.gate_reason = gate_reason
    paper.matched_groups = []
    if not passed:
        return -999

    full_text = f"{paper.title} {paper.abstract}"
    title_text = paper.title
    title_bonus = int(rules.get("title_bonus", 0))
    score = 0

    for group_name, group in rules.get("interest_groups", {}).items():
        terms = group.get("terms", [])
        matched = [term for term in terms if contains_term(full_text, str(term))]
        if not matched:
            continue

        paper.matched_groups.append(group_name)
        score += int(group.get("weight", 0))
        if title_bonus and any(contains_term(title_text, str(term)) for term in matched):
            score += title_bonus

    for category, bonus in rules.get("category_bonus", {}).items():
        if category in paper.categories:
            score += int(bonus)

    for group in rules.get("negative_groups", {}).values():
        if any(contains_term(full_text, str(term)) for term in group.get("terms", [])):
            score += int(group.get("weight", 0))

    return score


def filter_papers(papers: list[Paper], rules: dict[str, Any]) -> list[Paper]:
    threshold = int(rules.get("threshold", 5))
    selected: list[Paper] = []
    for paper in papers:
        paper.score = score_paper(paper, rules)
        if paper.score >= threshold:
            selected.append(paper)

    selected.sort(key=lambda p: (-p.score, p.title.lower()))
    return selected


def smtp_settings(user: str) -> tuple[str, int, bool]:
    """Return host, port, use_ssl. SMTP_HOST/SMTP_PORT can override auto detection."""
    override_host = os.getenv("SMTP_HOST", "").strip()
    override_port = os.getenv("SMTP_PORT", "").strip()
    override_mode = os.getenv("SMTP_MODE", "").strip().lower()

    if override_host:
        port = int(override_port or (465 if override_mode != "starttls" else 587))
        return override_host, port, override_mode != "starttls"

    domain = user.rsplit("@", 1)[-1].lower() if "@" in user else ""
    if domain in {"qq.com", "foxmail.com"}:
        return "smtp.qq.com", 465, True
    if domain == "gmail.com":
        return "smtp.gmail.com", 465, True
    if domain in {"outlook.com", "hotmail.com", "live.com"}:
        return "smtp-mail.outlook.com", 587, False
    if domain in {"163.com", "126.com"}:
        return f"smtp.{domain}", 465, True

    raise RuntimeError(
        "Unknown mail provider. Set SMTP_HOST, SMTP_PORT and optionally SMTP_MODE=starttls."
    )


def build_email(papers: list[Paper]) -> tuple[str, str, str]:
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    subject = f"arXiv Robotics Daily | {today} | {len(papers)} papers"

    plain_lines = [f"arXiv Robotics Daily — {today}", f"{len(papers)} selected papers", ""]
    html_items = []

    for idx, paper in enumerate(papers, start=1):
        plain_lines.extend([
            f"{idx}. {paper.title}",
            f"Authors: {paper.authors}",
            paper.link,
            "",
        ])
        html_items.append(
            "<div style='margin:0 0 22px 0;'>"
            f"<div style='font-size:16px;font-weight:600;line-height:1.45;'>{idx}. "
            f"<a href='{html.escape(paper.link)}' style='color:#0969da;text-decoration:none;'>"
            f"{html.escape(paper.title)}</a></div>"
            f"<div style='margin-top:5px;color:#57606a;line-height:1.45;'>"
            f"{html.escape(paper.authors)}</div>"
            f"<div style='margin-top:4px;'><a href='{html.escape(paper.link)}' "
            "style='color:#0969da;'>arXiv</a></div>"
            "</div>"
        )

    if not papers:
        plain_lines.append("No matching new papers today.")
        html_items.append("<p>No matching new papers today.</p>")

    html_body = (
        "<!doctype html><html><body style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif;"
        "max-width:760px;margin:28px auto;padding:0 20px;color:#1f2328;'>"
        f"<h2 style='margin-bottom:4px;'>arXiv Robotics Daily — {today}</h2>"
        f"<p style='margin-top:0;color:#57606a;'>{len(papers)} selected papers</p>"
        + "".join(html_items)
        + "</body></html>"
    )
    return subject, "\n".join(plain_lines), html_body


def send_email(subject: str, plain: str, html_body: str) -> None:
    user = os.getenv("MAIL_USER", "").strip()
    password = os.getenv("MAIL_PASSWORD", "").strip()
    recipient = os.getenv("MAIL_TO", "").strip()
    if not user or not password or not recipient:
        raise RuntimeError("MAIL_USER, MAIL_PASSWORD and MAIL_TO must be configured as GitHub Secrets.")

    host, port, use_ssl = smtp_settings(user)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient
    msg.set_content(plain)
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
            server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(user, password)
            server.send_message(msg)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print results without sending email")
    parser.add_argument("--rules", default="keywords.yaml")
    args = parser.parse_args()

    rules = load_rules(args.rules)
    papers = fetch_papers()
    selected = filter_papers(papers, rules)

    print(f"Fetched {len(papers)} unique papers across {len(CATEGORIES)} categories.")
    print(f"Selected {len(selected)} papers (threshold={rules.get('threshold', 5)}).")
    for paper in selected:
        cats = ",".join(sorted(paper.categories))
        groups = ",".join(paper.matched_groups)
        print(f"[{paper.score:>2}] [{cats}] [{paper.gate_reason}] [{groups}] {paper.title} | {paper.link}")

    subject, plain, html_body = build_email(selected)
    if args.dry_run:
        print("\n--- EMAIL PREVIEW ---\n")
        print(plain)
    else:
        send_email(subject, plain, html_body)
        print("Email sent successfully.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
