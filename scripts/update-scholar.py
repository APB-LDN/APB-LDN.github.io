#!/usr/bin/env python3
"""Fetch OpenAlex metrics for a single author.

Queries the OpenAlex authors and works endpoints for the supplied author ID
and writes aggregated citation information to ``src/data/scholar.json``.
"""

import json
import os
import sys
from typing import Dict, List

import requests

BASE_URL = "https://api.openalex.org"
EMAIL = "contact@apb-ldn.org"


def simplify(work: Dict) -> Dict:
    """Extract the fields we care about from a work record."""
    return {
        "id": work.get("id"),
        "display_name": work.get("display_name"),
        "publication_date": work.get("publication_date"),
        "cited_by_count": work.get("cited_by_count"),
    }


def get_author(aid: str) -> Dict:
    url = f"{BASE_URL}/authors/{aid}"
    resp = requests.get(url, params={"mailto": EMAIL}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_works(aid: str, sort_key: str) -> List[Dict]:
    url = f"{BASE_URL}/works"
    params = {
        "filter": f"authorships.author.id:{aid}",
        "sort": f"{sort_key}:desc",
        "per_page": 5,
        "mailto": EMAIL,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("results", [])


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/update-scholar.py <author_id>")
        sys.exit(1)

    aid = sys.argv[1].strip()
    try:
        author = get_author(aid)
        last_works = [simplify(w) for w in get_works(aid, "publication_date")]
        top_works = [simplify(w) for w in get_works(aid, "cited_by_count")]
        data = {
            "total_citations": author.get("cited_by_count", 0),
            "last_works": last_works,
            "top_cited_works": top_works,
        }
    except Exception as exc:
        print(f"OpenAlex request failed: {exc}")
        data = {"total_citations": 0, "last_works": [], "top_cited_works": []}

    os.makedirs("src/data", exist_ok=True)
    with open("src/data/scholar.json", "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


if __name__ == "__main__":
    main()
