#!/usr/bin/env python3
"""
update_content.py — Bi-weekly content discovery for apb-ldn.org

Sources
-------
1. OpenAlex API  — new academic publications    → data/news.json
2. Lawfare RSS   — new authored articles        → data/news.json + data/articles.json
3. GDELT Doc API — new media mentions (free)    → data/media.json

Usage
-----
  python scripts/update_content.py           # writes JSON files in-place
  DRY_RUN=1 python scripts/update_content.py # prints changes, no writes

On completion the script writes .update-summary.md (repo root) when at least
one new item was found. The GitHub Actions workflow uses this file as the PR
body; if the file is absent no PR is created.

The script is idempotent: it deduplicates by URL so re-running never creates
duplicate entries.
"""

import json
import os
import sys
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT    = Path(__file__).resolve().parent.parent
NEWS_FILE    = REPO_ROOT / "data" / "news.json"
MEDIA_FILE   = REPO_ROOT / "data" / "media.json"
ARTICLES_FILE = REPO_ROOT / "data" / "articles.json"
SUMMARY_FILE = REPO_ROOT / ".update-summary.md"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

OPENALEX_AUTHOR_ID = "A5101299489"
OPENALEX_URL = (
    "https://api.openalex.org/works"
    f"?filter=authorships.author.id:{OPENALEX_AUTHOR_ID}"
    "&sort=publication_date:desc&per-page=20"
    "&select=id,title,doi,publication_date,primary_location,cited_by_count"
    "&mailto=contact@apb-ldn.org"
)

LAWFARE_RSS    = "https://feeds.feedburner.com/lawfareblog"
LAWFARE_AUTHOR = "laudrain"

# GDELT Doc 2.0 — full-text search across ~65 000 news sources, no API key required.
# Returns up to 250 most-recent articles that mention the query string.
GDELT_URL = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=%22arthur%20laudrain%22"
    "&mode=artlist&format=json&maxrecords=50&sort=DateDesc"
)

# Domains to skip when processing GDELT results (aggregators / social / own site)
GDELT_SKIP_DOMAINS = {
    "reddit.com", "twitter.com", "x.com", "facebook.com", "linkedin.com",
    "wikipedia.org", "youtube.com", "researchgate.net", "academia.edu",
    "apb-ldn.org", "semanticscholar.org", "openalex.org", "scholar.google.com",
    "muckrack.com", "isni.org", "orcid.org", "crossref.org", "lens.org",
}

DRY_RUN = os.environ.get("DRY_RUN", "0").strip() not in ("", "0", "false", "no")

# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def fetch_url(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "APB-LDN-content-updater/1.0 (contact@apb-ldn.org)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, items: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)
    print(f"  → wrote {len(items)} items to {path.name}")


def known_links(items: list[dict]) -> set[str]:
    return {item["link"].rstrip("/") for item in items if item.get("link")}


# ---------------------------------------------------------------------------
# Source 1: OpenAlex → news.json  (academic publications)
# ---------------------------------------------------------------------------


def fetch_openalex(seen: set[str]) -> list[dict]:
    new: list[dict] = []
    try:
        data = json.loads(fetch_url(OPENALEX_URL))
    except Exception as exc:
        print(f"[OpenAlex] fetch error: {exc}", file=sys.stderr)
        return new

    for work in data.get("results", []):
        doi  = work.get("doi") or ""
        loc  = work.get("primary_location") or {}
        src  = (loc.get("source") or {}).get("display_name") or ""
        link = (loc.get("landing_page_url") or doi or work.get("id", "")).rstrip("/")

        if not link or link in seen:
            continue

        title = work.get("title") or "Untitled"
        date  = work.get("publication_date") or ""
        desc  = f"Academic publication{f' in {src}' if src else ''}."

        new.append({"type": "publication", "title": title, "date": date,
                    "description": desc, "link": link})
        seen.add(link)
        print(f"[OpenAlex]  + {title[:80]}")

    return new


# ---------------------------------------------------------------------------
# Source 2: Lawfare RSS → news.json + articles.json  (policy articles)
# ---------------------------------------------------------------------------


def fetch_lawfare(seen_news: set[str], seen_articles: set[str]) -> tuple[list[dict], list[dict]]:
    new_news:     list[dict] = []
    new_articles: list[dict] = []

    try:
        raw = fetch_url(LAWFARE_RSS)
    except Exception as exc:
        print(f"[Lawfare] fetch error: {exc}", file=sys.stderr)
        return new_news, new_articles

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        print(f"[Lawfare] XML error: {exc}", file=sys.stderr)
        return new_news, new_articles

    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    for item in root.findall(".//item"):
        creator = (item.findtext("dc:creator", namespaces=ns) or "").lower()
        if LAWFARE_AUTHOR not in creator:
            continue

        link = (item.findtext("link") or "").strip().rstrip("/")
        if not link:
            continue

        title = item.findtext("title") or "Untitled"

        pub_date = ""
        raw_date = item.findtext("pubDate") or ""
        try:
            from email.utils import parsedate_to_datetime
            pub_date = parsedate_to_datetime(raw_date).strftime("%Y-%m-%d")
        except Exception:
            pass

        if link not in seen_news:
            new_news.append({
                "type": "article",
                "title": title,
                "date": pub_date,
                "description": "Article published on Lawfare.",
                "link": link,
            })
            seen_news.add(link)
            print(f"[Lawfare/news]     + {title[:70]}")

        if link not in seen_articles:
            new_articles.append({
                "title": title,
                "source": "Lawfare",
                "type": "article",
                "date": pub_date,
                "description": "Article published on Lawfare.",
                "link": link,
                "image": None,
            })
            seen_articles.add(link)
            print(f"[Lawfare/articles] + {title[:70]}")

    return new_news, new_articles


# ---------------------------------------------------------------------------
# Source 3: GDELT Doc API → media.json  (press mentions)
# ---------------------------------------------------------------------------


def _clean_outlet(domain: str) -> str:
    """Best-effort human-readable outlet name from a domain."""
    name = domain.removeprefix("www.")
    # Strip ccTLD suffixes for well-known outlets
    name = name.replace(".co.uk", "").replace(".com", "").replace(".org", "")
    name = name.replace(".net", "").replace(".fr", "").replace(".de", "")
    name = name.replace(".", " ")
    return name.title().strip()


def fetch_gdelt(seen: set[str]) -> list[dict]:
    new: list[dict] = []
    try:
        data = json.loads(fetch_url(GDELT_URL))
    except Exception as exc:
        print(f"[GDELT] fetch error: {exc}", file=sys.stderr)
        return new

    for article in data.get("articles") or []:
        url = (article.get("url") or "").strip().rstrip("/")
        if not url or url in seen:
            continue

        domain = article.get("domain") or urllib.parse.urlparse(url).netloc
        if any(skip in domain for skip in GDELT_SKIP_DOMAINS):
            continue

        # Parse seendate: "20230117T120000Z" or "20230117120000"
        raw_date = (article.get("seendate") or "").replace("T", "").replace("Z", "")
        date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) >= 8 else ""

        title  = (article.get("title") or "Untitled").strip()
        outlet = _clean_outlet(domain)

        new.append({
            "outlet": outlet,
            "type": "quote",
            "title": title,
            "date": date,
            "description": None,
            "link": url,
        })
        seen.add(url)
        print(f"[GDELT]  + [{outlet}] {title[:60]}")

    return new


# ---------------------------------------------------------------------------
# Summary markdown
# ---------------------------------------------------------------------------


def _md_list(items: list[dict], link_key: str = "link", title_key: str = "title",
             date_key: str = "date", extra_key: str | None = None) -> str:
    lines = []
    for it in items:
        link  = it.get(link_key) or ""
        title = it.get(title_key) or "Untitled"
        date  = (it.get(date_key) or "")[:7]          # YYYY-MM
        extra = it.get(extra_key) if extra_key else None
        parts = [date, extra] if extra else [date]
        meta  = " · ".join(p for p in parts if p)
        entry = f"[{title}]({link})" if link else title
        lines.append(f"- {entry}" + (f" — {meta}" if meta else ""))
    return "\n".join(lines) if lines else "_none_"


def write_summary(
    new_publications: list[dict],
    new_articles_news: list[dict],
    new_articles: list[dict],
    new_media: list[dict],
    run_date: str,
) -> None:
    total = len(new_publications) + len(new_articles_news) + len(new_articles) + len(new_media)
    lines = [
        f"## Bi-weekly content update — {run_date}",
        f"",
        f"**{total} new item(s) found** across all sources. "
        f"Review each entry below, then merge to publish — or close to discard.",
        f"",
        f"---",
        f"",
        f"### Publications ({len(new_publications)} new → `data/news.json`)",
        _md_list(new_publications, extra_key="description"),
        f"",
        f"### Lawfare articles ({len(new_articles_news)} new → `data/news.json` + `data/articles.json`)",
        _md_list(new_articles_news),
        f"",
        f"### Media mentions ({len(new_media)} new → `data/media.json`)",
        _md_list(new_media, extra_key="outlet"),
        f"",
        f"---",
        f"",
        f"_Discovered automatically by `scripts/update_content.py`. "
        f"Links, dates, and descriptions may need manual review._",
    ]
    SUMMARY_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSummary written to {SUMMARY_FILE.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    from datetime import date as _date
    run_date = _date.today().isoformat()

    if DRY_RUN:
        print("DRY RUN — JSON files will not be written.\n")

    # Load current state
    news     = load_json(NEWS_FILE)
    media    = load_json(MEDIA_FILE)
    articles = load_json(ARTICLES_FILE)

    seen_news     = known_links(news)
    seen_media    = known_links(media)
    seen_articles = known_links(articles)

    # Fetch from all sources
    print("=== OpenAlex (publications) ===")
    new_pubs = fetch_openalex(seen_news)

    print("\n=== Lawfare RSS (articles) ===")
    new_lawfare_news, new_lawfare_articles = fetch_lawfare(seen_news, seen_articles)

    print("\n=== GDELT (media mentions) ===")
    new_media_items = fetch_gdelt(seen_media)

    # Summarise
    total = len(new_pubs) + len(new_lawfare_news) + len(new_lawfare_articles) + len(new_media_items)
    print(f"\n{'─'*50}")
    print(f"Found: {len(new_pubs)} publication(s), "
          f"{len(new_lawfare_news)} Lawfare article(s), "
          f"{len(new_media_items)} media mention(s)")

    if total == 0:
        print("Nothing new — no files written, no PR will be created.")
        # Ensure no stale summary from a previous run triggers a false PR
        SUMMARY_FILE.unlink(missing_ok=True)
        return

    if not DRY_RUN:
        # news.json: prepend new items, sort by date desc
        all_news = new_pubs + new_lawfare_news + news
        all_news.sort(key=lambda x: x.get("date") or "", reverse=True)
        save_json(NEWS_FILE, all_news)

        # media.json: prepend new items, sort by date desc
        if new_media_items:
            all_media = new_media_items + media
            all_media.sort(key=lambda x: x.get("date") or "", reverse=True)
            save_json(MEDIA_FILE, all_media)

        # articles.json: prepend new items, sort by date desc
        if new_lawfare_articles:
            all_articles = new_lawfare_articles + articles
            all_articles.sort(key=lambda x: x.get("date") or "", reverse=True)
            save_json(ARTICLES_FILE, all_articles)

        write_summary(new_pubs, new_lawfare_news, new_lawfare_articles, new_media_items, run_date)
    else:
        print("\n--- DRY RUN: would write ---")
        print(json.dumps({
            "news": new_pubs + new_lawfare_news,
            "articles": new_lawfare_articles,
            "media": new_media_items,
        }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
