#!/usr/bin/env python3
"""Build and send the daily arXiv digest with Chinese title/abstract translations."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fetch_and_mail import TIMEZONE, USER_AGENT, fetch_papers, filter_papers, load_rules, send_email


TRANSLATION_FALLBACK = "（翻译暂不可用）"
TRANSLATE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"


def split_for_translation(text: str, max_chars: int = 2400) -> list[str]:
    """Split long abstracts at sentence/whitespace boundaries to keep requests small."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(sentence), max_chars):
                chunks.append(sentence[start : start + max_chars])
            continue
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def translate_chunk(text: str) -> str:
    params = urlencode({"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text})
    request = Request(
        f"{TRANSLATE_ENDPOINT}?{params}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urlopen(request, timeout=6) as response:
        payload = json.loads(response.read().decode("utf-8"))
    translated = "".join(part[0] for part in payload[0] if part and part[0])
    return translated.strip()


def translate_text(text: str) -> str:
    chunks = split_for_translation(text)
    if not chunks:
        return ""

    translated_chunks: list[str] = []
    for chunk in chunks:
        translated = ""
        for attempt in range(2):
            try:
                translated = translate_chunk(chunk)
                lowered = translated.lower()
                if translated and "server error" not in lowered and "that's an error" not in lowered:
                    break
                translated = ""
            except Exception as exc:
                print(f"Warning: translation attempt {attempt + 1} failed: {exc}", file=sys.stderr)
            time.sleep(0.5 * (attempt + 1))

        if not translated:
            return ""
        translated_chunks.append(translated)
        time.sleep(0.08)

    return " ".join(translated_chunks).strip()


def build_translated_email(papers) -> tuple[str, str, str]:
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    subject = f"arXiv Robotics Daily | {today} | {len(papers)} papers"
    plain_lines = [f"arXiv Robotics Daily — {today}", f"{len(papers)} selected papers", ""]
    html_items: list[str] = []

    for idx, paper in enumerate(papers, start=1):
        print(f"Translating {idx}/{len(papers)}: {paper.title}")
        zh_title = translate_text(paper.title)
        zh_abstract = translate_text(paper.abstract)

        plain_lines.extend(
            [
                f"{idx}. {paper.title}",
                f"中文标题：{zh_title or TRANSLATION_FALLBACK}",
                f"Authors: {paper.authors}",
                "",
                "Abstract:",
                paper.abstract or "(No abstract available)",
                "",
                "摘要翻译：",
                zh_abstract or TRANSLATION_FALLBACK,
                "",
                paper.link,
                "",
                "-" * 72,
                "",
            ]
        )

        abstract_en = paper.abstract or "(No abstract available)"
        abstract_zh = zh_abstract or TRANSLATION_FALLBACK
        title_zh = zh_title or TRANSLATION_FALLBACK

        html_items.append(
            "<div style='margin:0 0 30px 0;padding-bottom:26px;border-bottom:1px solid #d8dee4;'>"
            f"<div style='font-size:17px;font-weight:650;line-height:1.45;'>{idx}. "
            f"<a href='{html.escape(paper.link)}' style='color:#0969da;text-decoration:none;'>"
            f"{html.escape(paper.title)}</a></div>"
            f"<div style='margin-top:5px;font-size:16px;font-weight:600;line-height:1.5;'>"
            f"{html.escape(title_zh)}</div>"
            f"<div style='margin-top:7px;color:#57606a;line-height:1.45;'>"
            f"{html.escape(paper.authors)}</div>"
            "<div style='margin-top:14px;font-size:13px;font-weight:700;color:#57606a;'>ABSTRACT</div>"
            f"<div style='margin-top:5px;line-height:1.65;'>{html.escape(abstract_en)}</div>"
            "<div style='margin-top:14px;font-size:13px;font-weight:700;color:#57606a;'>摘要翻译</div>"
            f"<div style='margin-top:5px;line-height:1.75;'>{html.escape(abstract_zh)}</div>"
            f"<div style='margin-top:12px;'><a href='{html.escape(paper.link)}' "
            "style='color:#0969da;'>arXiv</a></div>"
            "</div>"
        )

    if not papers:
        plain_lines.append("No matching new papers today.")
        html_items.append("<p>No matching new papers today.</p>")

    html_body = (
        "<!doctype html><html><body style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,"
        "PingFang SC,Microsoft YaHei,sans-serif;max-width:820px;margin:28px auto;padding:0 20px;color:#1f2328;'>"
        f"<h2 style='margin-bottom:4px;'>arXiv Robotics Daily — {today}</h2>"
        f"<p style='margin-top:0;color:#57606a;'>{len(papers)} selected papers · English + 中文</p>"
        + "".join(html_items)
        + "</body></html>"
    )
    return subject, "\n".join(plain_lines), html_body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print email preview without sending")
    parser.add_argument("--rules", default="keywords.yaml")
    args = parser.parse_args()

    rules = load_rules(args.rules)
    papers = fetch_papers()
    selected = filter_papers(papers, rules)

    print(f"Fetched {len(papers)} unique papers.")
    print(f"Selected {len(selected)} papers for translated digest.")
    for paper in selected:
        print(f"[{paper.score:>2}] {paper.title} | {paper.link}")

    subject, plain, html_body = build_translated_email(selected)
    if args.dry_run:
        print("\n--- TRANSLATED EMAIL PREVIEW ---\n")
        print(plain)
    else:
        send_email(subject, plain, html_body)
        print("Translated digest email sent successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
