#!/usr/bin/env python3
"""
update_content.py — Bi-weekly content discovery for apb-ldn.org

Sources (automated)
-------------------
1. OpenAlex API       — academic publications         → data/news.json
2. Semantic Scholar   — additional academic works     → data/news.json
3. Lawfare RSS        — authored policy articles      → data/news.json + data/articles.json
4. GDELT Doc API      — press / media mentions        → data/media.json

Source (manual queue)
---------------------
5. data/queue.json    — manually staged items         → routed by "target" field
   Add items here (or via the manual-update workflow) for events, TV/radio
   appearances, or anything not yet discoverable online.
   Consumed entries are removed from the queue after processing.

Usage
-----
  python scripts/update_content.py           # writes JSON files in-place
  DRY_RUN=1 python scripts/update_content.py # prints changes, no writes

On completion the script writes .update-summary.md (repo root) when at least
one new item was found. The GitHub Actions workflow uses this file as the PR
body; if the file is absent no PR is created.

Idempotent: deduplication by URL ensures re-running never creates duplicates.
"""

import hashlib
import json
import os
import re
import sys
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT     = Path(__file__).resolve().parent.parent
NEWS_FILE     = REPO_ROOT / "data" / "news.json"
MEDIA_FILE    = REPO_ROOT / "data" / "media.json"
ARTICLES_FILE = REPO_ROOT / "data" / "articles.json"
QUEUE_FILE    = REPO_ROOT / "data" / "queue.json"
IMAGES_DIR    = REPO_ROOT / "assets" / "images"
SUMMARY_FILE  = REPO_ROOT / ".update-summary.md"

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

# Semantic Scholar — free API, no key required.
# We search by name to resolve the author ID, then fetch their papers.
S2_AUTHOR_SEARCH = (
    "https://api.semanticscholar.org/graph/v1/author/search"
    "?query=arthur+laudrain&fields=authorId,name,affiliations&limit=3"
)
S2_PAPERS_TMPL = (
    "https://api.semanticscholar.org/graph/v1/author/{author_id}/papers"
    "?fields=title,externalIds,publicationDate,venue,url&limit=20"
)

LAWFARE_RSS    = "https://feeds.feedburner.com/lawfareblog"
LAWFARE_AUTHOR = "laudrain"

# GDELT Doc 2.0 — ~65 000 sources, no API key required.
# timespan=30days keeps each run focused on recent coverage only;
# deduplication handles the rare case of re-discovering older items.
GDELT_URL = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=%22arthur%20laudrain%22"
    "&mode=artlist&format=json&maxrecords=50&sort=DateDesc&timespan=30days"
)

# Domains to skip for GDELT (aggregators / social / academic indices / own site)
GDELT_SKIP_DOMAINS = {
    "reddit.com", "twitter.com", "x.com", "facebook.com", "linkedin.com",
    "wikipedia.org", "youtube.com", "researchgate.net", "academia.edu",
    "apb-ldn.org", "semanticscholar.org", "openalex.org", "scholar.google.com",
    "muckrack.com", "isni.org", "orcid.org", "crossref.org", "lens.org",
    "isnblog.ethz.ch",
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
        data = json.load(fh)
    return data if isinstance(data, list) else []


def save_json(path: Path, items: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=2)
    print(f"  → wrote {len(items)} items to {path.name}")


def known_links(items: list[dict]) -> set[str]:
    return {item["link"].rstrip("/") for item in items if item.get("link")}


# ---------------------------------------------------------------------------
# Image download (used by queue processing)
# ---------------------------------------------------------------------------


def _image_ext(url: str, content_type: str = "") -> str:
    """Infer a file extension from URL or Content-Type header."""
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"):
        if url.lower().split("?")[0].endswith(ext):
            return ext.replace(".jpeg", ".jpg")
    if "png" in content_type:
        return ".png"
    if "svg" in content_type:
        return ".svg"
    if "webp" in content_type:
        return ".webp"
    return ".jpg"


def download_image(image_url: str, title: str) -> str | None:
    """Download image_url, save to assets/images/, return local path or None."""
    try:
        req = urllib.request.Request(
            image_url,
            headers={"User-Agent": "APB-LDN-content-updater/1.0"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data  = resp.read()
            ctype = resp.headers.get("Content-Type", "")
        ext   = _image_ext(image_url, ctype)
        slug  = re.sub(r"[^a-z0-9]+", "-", title.lower())[:30].strip("-")
        hash8 = hashlib.md5(image_url.encode()).hexdigest()[:8]
        fname = f"queue-{slug}-{hash8}{ext}"
        dest  = IMAGES_DIR / fname
        dest.write_bytes(data)
        local = f"assets/images/{fname}"
        print(f"  [image] downloaded → {local}")
        return local
    except Exception as exc:
        print(f"  [image] download failed for {image_url}: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Source 0: data/queue.json  (manual queue)
# ---------------------------------------------------------------------------


def process_queue(
    seen_news: set[str],
    seen_media: set[str],
    seen_articles: set[str],
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """
    Read data/queue.json, route each entry to the appropriate list, download
    images where requested. Returns (queue_news, queue_media, queue_articles,
    remaining_queue) where remaining_queue contains any unprocessable items.
    """
    queue = load_json(QUEUE_FILE)
    if not queue:
        return [], [], [], []

    q_news:     list[dict] = []
    q_media:    list[dict] = []
    q_articles: list[dict] = []
    remaining:  list[dict] = []

    for entry in queue:
        target = (entry.get("target") or "").lower()
        link   = (entry.get("link") or "").rstrip("/") or None
        title  = entry.get("title") or "Untitled"

        # Download image if image_url provided
        image_url = entry.pop("image_url", None)
        if image_url and not DRY_RUN:
            local = download_image(image_url, title)
            if local:
                entry["image"] = local

        if target == "news":
            if link and link in seen_news:
                print(f"[Queue/news] skip (duplicate): {title[:60]}")
                continue
            item = {
                "type":        entry.get("type") or "conference",
                "title":       title,
                "date":        entry.get("date") or "",
                "description": entry.get("description") or "",
                "link":        link,
            }
            for opt in ("image", "embed", "embed_title", "extra_images"):
                if entry.get(opt):
                    item[opt] = entry[opt]
            q_news.append(item)
            if link:
                seen_news.add(link)
            print(f"[Queue/news]     + {title[:70]}")

        elif target == "media":
            if link and link in seen_media:
                print(f"[Queue/media] skip (duplicate): {title[:60]}")
                continue
            item = {
                "outlet":      entry.get("outlet") or "Unknown",
                "type":        entry.get("type") or "quote",
                "title":       title,
                "date":        entry.get("date") or "",
                "description": entry.get("description") or None,
                "link":        link,
            }
            q_media.append(item)
            if link:
                seen_media.add(link)
            print(f"[Queue/media]    + [{item['outlet']}] {title[:60]}")

        elif target == "article":
            if link and link in seen_articles:
                print(f"[Queue/article] skip (duplicate): {title[:60]}")
                continue
            item = {
                "title":       title,
                "source":      entry.get("source") or "Unknown",
                "type":        entry.get("type") or "article",
                "date":        entry.get("date") or "",
                "description": entry.get("description") or "",
                "link":        link,
                "image":       entry.get("image") or None,
            }
            q_articles.append(item)
            if link:
                seen_articles.add(link)
            print(f"[Queue/article]  + [{item['source']}] {title[:60]}")

        else:
            print(f"[Queue] unknown target '{target}' for '{title[:50]}' — kept in queue",
                  file=sys.stderr)
            remaining.append(entry)

    return q_news, q_media, q_articles, remaining


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
# Source 2: Semantic Scholar → news.json  (additional academic works)
# ---------------------------------------------------------------------------


def fetch_semantic_scholar(seen: set[str]) -> list[dict]:
    """
    Search Semantic Scholar for Arthur Laudrain's papers. Resolves the author
    ID dynamically so no hard-coded S2 ID is needed.
    """
    new: list[dict] = []
    try:
        authors = json.loads(fetch_url(S2_AUTHOR_SEARCH)).get("data") or []
    except Exception as exc:
        print(f"[S2] author search error: {exc}", file=sys.stderr)
        return new

    # Pick the first result whose name matches (case-insensitive)
    author_id = None
    for a in authors:
        if "laudrain" in (a.get("name") or "").lower():
            author_id = a.get("authorId")
            break

    if not author_id:
        print("[S2] author not found in search results", file=sys.stderr)
        return new

    try:
        papers_url = S2_PAPERS_TMPL.format(author_id=author_id)
        data = json.loads(fetch_url(papers_url))
    except Exception as exc:
        print(f"[S2] papers fetch error: {exc}", file=sys.stderr)
        return new

    for paper in data.get("data") or []:
        doi  = (paper.get("externalIds") or {}).get("DOI") or ""
        link = (f"https://doi.org/{doi}" if doi else paper.get("url") or "").rstrip("/")

        if not link or link in seen:
            continue

        title = paper.get("title") or "Untitled"
        date  = paper.get("publicationDate") or ""
        venue = (paper.get("venue") or {}).get("name") if isinstance(paper.get("venue"), dict) else (paper.get("venue") or "")
        desc  = f"Academic publication{f' in {venue}' if venue else ''}."

        new.append({"type": "publication", "title": title, "date": date,
                    "description": desc, "link": link})
        seen.add(link)
        print(f"[S2]        + {title[:80]}")

    return new


# ---------------------------------------------------------------------------
# Source 3: Lawfare RSS → news.json + articles.json  (policy articles)
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
                "type": "article", "title": title, "date": pub_date,
                "description": "Article published on Lawfare.", "link": link,
            })
            seen_news.add(link)
            print(f"[Lawfare/news]     + {title[:70]}")

        if link not in seen_articles:
            new_articles.append({
                "title": title, "source": "Lawfare", "type": "article",
                "date": pub_date, "description": "Article published on Lawfare.",
                "link": link, "image": None,
            })
            seen_articles.add(link)
            print(f"[Lawfare/articles] + {title[:70]}")

    return new_news, new_articles


# ---------------------------------------------------------------------------
# Source 4: GDELT Doc API → media.json  (press mentions)
# ---------------------------------------------------------------------------


def _clean_outlet(domain: str) -> str:
    name = domain.removeprefix("www.")
    for suffix in (".co.uk", ".com", ".org", ".net", ".fr", ".de", ".ch"):
        name = name.replace(suffix, "")
    return name.replace(".", " ").title().strip()


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

        raw_date = (article.get("seendate") or "").replace("T", "").replace("Z", "")
        date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) >= 8 else ""

        title  = (article.get("title") or "Untitled").strip()
        outlet = _clean_outlet(domain)

        new.append({
            "outlet": outlet, "type": "quote", "title": title,
            "date": date, "description": None, "link": url,
        })
        seen.add(url)
        print(f"[GDELT]  + [{outlet}] {title[:60]}")

    return new


# ---------------------------------------------------------------------------
# Summary markdown
# ---------------------------------------------------------------------------


def _field(label: str, value: str | None) -> str:
    """One metadata line inside a detail block."""
    return f"  - **{label}:** {value or '—'}"


def _detail_block(items: list[dict], kind: str) -> str:
    """
    Render every item as a fully-detailed block so the reviewer can verify
    every field that will be written to JSON before merging.
    """
    if not items:
        return "_none_"
    lines = []
    for it in items:
        link  = it.get("link") or ""
        title = it.get("title") or "Untitled"
        entry = f"[{title}]({link})" if link else f"**{title}**"
        lines.append(f"- {entry}")

        if kind == "news":
            lines.append(_field("date",        it.get("date")))
            lines.append(_field("type",        it.get("type")))
            lines.append(_field("description", it.get("description")))

        elif kind == "media":
            lines.append(_field("outlet",      it.get("outlet")))
            lines.append(_field("date",        it.get("date")))
            lines.append(_field("type",        it.get("type")))
            lines.append(_field("description", it.get("description")))

        elif kind == "article":
            lines.append(_field("source",      it.get("source")))
            lines.append(_field("date",        it.get("date")))
            lines.append(_field("type",        it.get("type")))
            lines.append(_field("description", it.get("description")))
            lines.append(_field("image",       it.get("image")))

        lines.append("")   # blank line between items for readability
    return "\n".join(lines).rstrip()


def write_summary(buckets: dict[str, list[dict]], queue_counts: dict[str, int],
                  run_date: str, is_manual: bool = False) -> None:
    new_pubs      = buckets.get("pubs", [])
    new_s2        = buckets.get("s2", [])
    new_lf_news   = buckets.get("lawfare_news", [])
    new_media     = buckets.get("media", [])
    q_news        = buckets.get("q_news", [])
    q_media       = buckets.get("q_media", [])
    q_articles    = buckets.get("q_articles", [])

    total = sum(len(v) for v in buckets.values())
    kind  = "Manual content update" if is_manual else "Bi-weekly content update"

    lines = [
        f"## {kind} — {run_date}",
        f"",
        f"**{total} new item(s).** Review every field below, then merge to publish — or close to discard.",
        f"",
        f"> Each block shows exactly what will be written to JSON.",
        f"> Pay particular attention to **outlet** names (auto-derived from domain) and **description** text.",
        f"",
        f"---",
        f"",
        f"### From queue (`data/queue.json`) — {len(q_news) + len(q_media) + len(q_articles)} item(s)",
        f"",
        f"#### News ({len(q_news)} → `data/news.json`)",
        f"",
        _detail_block(q_news, "news"),
        f"",
        f"#### Media ({len(q_media)} → `data/media.json`)",
        f"",
        _detail_block(q_media, "media"),
        f"",
        f"#### Articles ({len(q_articles)} → `data/articles.json`)",
        f"",
        _detail_block(q_articles, "article"),
        f"",
        f"---",
        f"",
        f"### Automated discovery",
        f"",
        f"#### OpenAlex publications ({len(new_pubs)} new → `data/news.json`)",
        f"",
        _detail_block(new_pubs, "news"),
        f"",
        f"#### Semantic Scholar ({len(new_s2)} new → `data/news.json`)",
        f"",
        _detail_block(new_s2, "news"),
        f"",
        f"#### Lawfare articles ({len(new_lf_news)} new → `data/news.json` + `data/articles.json`)",
        f"",
        _detail_block(new_lf_news, "news"),
        f"",
        f"#### Media mentions via GDELT ({len(new_media)} new → `data/media.json`)",
        f"",
        _detail_block(new_media, "media"),
        f"",
        f"---",
        f"",
        f"_Auto-generated by `scripts/update_content.py`._",
    ]
    SUMMARY_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSummary written to {SUMMARY_FILE.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    from datetime import date as _date
    run_date  = _date.today().isoformat()
    is_manual = os.environ.get("MANUAL_RUN", "0") not in ("", "0", "false", "no")

    if DRY_RUN:
        print("DRY RUN — JSON files will not be written.\n")

    # Load current state
    news     = load_json(NEWS_FILE)
    media    = load_json(MEDIA_FILE)
    articles = load_json(ARTICLES_FILE)

    seen_news     = known_links(news)
    seen_media    = known_links(media)
    seen_articles = known_links(articles)

    # --- Queue (process first so manual items always make it into the PR) ---
    print("=== Manual queue (data/queue.json) ===")
    q_news, q_media, q_articles, remaining_queue = process_queue(
        seen_news, seen_media, seen_articles
    )

    # --- Automated sources ---
    print("\n=== OpenAlex (publications) ===")
    new_pubs = fetch_openalex(seen_news)

    print("\n=== Semantic Scholar (publications) ===")
    new_s2 = fetch_semantic_scholar(seen_news)

    print("\n=== Lawfare RSS (articles) ===")
    new_lf_news, new_lf_articles = fetch_lawfare(seen_news, seen_articles)

    print("\n=== GDELT (media mentions, last 30 days) ===")
    new_media_items = fetch_gdelt(seen_media)

    # --- Summarise ---
    buckets = {
        "q_news":       q_news,
        "q_media":      q_media,
        "q_articles":   q_articles,
        "pubs":         new_pubs,
        "s2":           new_s2,
        "lawfare_news": new_lf_news,
        "media":        new_media_items,
    }
    total = sum(len(v) for v in buckets.values())

    print(f"\n{'─'*55}")
    print(f"Queue:    {len(q_news)} news, {len(q_media)} media, {len(q_articles)} articles")
    print(f"Auto:     {len(new_pubs)} OpenAlex, {len(new_s2)} S2, "
          f"{len(new_lf_news)} Lawfare, {len(new_media_items)} GDELT")
    print(f"Total:    {total} new item(s)")

    if total == 0:
        print("Nothing new — no files written, no PR will be created.")
        SUMMARY_FILE.unlink(missing_ok=True)
        # Still clear a now-empty queue
        if not DRY_RUN and QUEUE_FILE.exists():
            save_json(QUEUE_FILE, remaining_queue)
        return

    if not DRY_RUN:
        # news.json
        all_news = q_news + new_pubs + new_s2 + new_lf_news + news
        all_news.sort(key=lambda x: x.get("date") or "", reverse=True)
        save_json(NEWS_FILE, all_news)

        # media.json
        if q_media or new_media_items:
            all_media = q_media + new_media_items + media
            all_media.sort(key=lambda x: x.get("date") or "", reverse=True)
            save_json(MEDIA_FILE, all_media)

        # articles.json
        if q_articles or new_lf_articles:
            all_articles = q_articles + new_lf_articles + articles
            all_articles.sort(key=lambda x: x.get("date") or "", reverse=True)
            save_json(ARTICLES_FILE, all_articles)

        # Clear consumed queue entries
        save_json(QUEUE_FILE, remaining_queue)

        write_summary(buckets, {}, run_date, is_manual=is_manual)
    else:
        print("\n--- DRY RUN: would write ---")
        print(json.dumps({k: v for k, v in buckets.items() if v},
                         indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
