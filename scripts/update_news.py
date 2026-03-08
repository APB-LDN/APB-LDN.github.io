#!/usr/bin/env python3
"""
update_news.py — Automatically add new items to data/news.json.

Sources:
  1. OpenAlex API — new academic publications for author A5101299489
  2. Lawfare RSS feed — new articles authored/co-authored by Laudrain

Usage:
  python scripts/update_news.py           # writes data/news.json in-place
  DRY_RUN=1 python scripts/update_news.py # prints changes only, no writes

The script is idempotent: it deduplicates by URL (link field) so running it
multiple times will not create duplicate entries.
"""

import json
import os
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
NEWS_FILE = REPO_ROOT / "data" / "news.json"

OPENALEX_AUTHOR_ID = "A5101299489"
OPENALEX_URL = (
    "https://api.openalex.org/works"
    f"?filter=authorships.author.id:{OPENALEX_AUTHOR_ID}"
    "&sort=publication_date:desc"
    "&per-page=20"
    "&select=id,title,doi,publication_date,primary_location,cited_by_count"
    "&mailto=contact@apb-ldn.org"
)

LAWFARE_RSS = "https://feeds.feedburner.com/lawfareblog"
LAWFARE_AUTHOR_PATTERN = "laudrain"

DRY_RUN = os.environ.get("DRY_RUN", "0").strip() not in ("", "0", "false", "no")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fetch_url(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "APB-LDN-news-updater/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def load_news() -> list[dict]:
    if not NEWS_FILE.exists():
        return []
    with open(NEWS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def save_news(items: list[dict]) -> None:
    with open(NEWS_FILE, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)
    print(f"Wrote {len(items)} items to {NEWS_FILE}")


def existing_links(items: list[dict]) -> set[str]:
    return {item["link"].rstrip("/") for item in items if item.get("link")}


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------


def fetch_openalex(known_links: set[str]) -> list[dict]:
    new_items: list[dict] = []
    try:
        raw = fetch_url(OPENALEX_URL)
        data = json.loads(raw)
    except Exception as exc:
        print(f"[OpenAlex] Error fetching: {exc}", file=sys.stderr)
        return new_items

    for work in data.get("results", []):
        doi = work.get("doi") or ""
        loc = work.get("primary_location") or {}
        source = loc.get("source") or {}
        landing = loc.get("landing_page_url") or doi or work.get("id", "")
        link = landing.rstrip("/")

        if not link or link in known_links:
            continue

        title = work.get("title") or "Untitled"
        pub_date = work.get("publication_date") or ""
        venue = source.get("display_name") or ""
        desc = f"Academic publication{f' in {venue}' if venue else ''}."

        item = {
            "type": "publication",
            "title": title,
            "date": pub_date,
            "description": desc,
            "link": link,
        }
        new_items.append(item)
        known_links.add(link)
        print(f"[OpenAlex] + {title[:80]}")

    return new_items


# ---------------------------------------------------------------------------
# Lawfare RSS
# ---------------------------------------------------------------------------


def fetch_lawfare(known_links: set[str]) -> list[dict]:
    new_items: list[dict] = []
    try:
        raw = fetch_url(LAWFARE_RSS)
    except Exception as exc:
        print(f"[Lawfare] Error fetching RSS: {exc}", file=sys.stderr)
        return new_items

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        print(f"[Lawfare] XML parse error: {exc}", file=sys.stderr)
        return new_items

    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    items = root.findall(".//item")
    for item in items:
        creator = (item.findtext("dc:creator", namespaces=ns) or "").lower()
        if LAWFARE_AUTHOR_PATTERN not in creator:
            continue

        link = (item.findtext("link") or "").strip().rstrip("/")
        if not link or link in known_links:
            continue

        title = item.findtext("title") or "Untitled"
        pub_date_raw = item.findtext("pubDate") or ""
        # Parse RFC 2822 date from RSS
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(pub_date_raw)
            pub_date = dt.strftime("%Y-%m-%d")
        except Exception:
            pub_date = ""

        new_items.append({
            "type": "article",
            "title": title,
            "date": pub_date,
            "description": "Article published on Lawfare.",
            "link": link,
        })
        known_links.add(link)
        print(f"[Lawfare] + {title[:80]}")

    return new_items


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if DRY_RUN:
        print("DRY RUN — no files will be written.\n")

    items = load_news()
    known = existing_links(items)

    new_items: list[dict] = []
    new_items.extend(fetch_openalex(known))
    new_items.extend(fetch_lawfare(known))

    if not new_items:
        print("No new items found.")
        return

    print(f"\n{len(new_items)} new item(s) found.")
    all_items = new_items + items  # prepend newest first
    # Re-sort by date descending
    all_items.sort(key=lambda x: x.get("date") or "", reverse=True)

    if DRY_RUN:
        print(json.dumps(new_items, indent=2, ensure_ascii=False))
    else:
        save_news(all_items)


if __name__ == "__main__":
    main()
