# data/ — content files for apb-ldn.org

All files are JSON arrays sorted by `date` descending.
The website renders each file dynamically via JavaScript; no HTML edits are needed.

---

## Files

| File | Rendered in | Updated by |
|---|---|---|
| `news.json` | **Latest news** section | Automated + queue |
| `media.json` | **Media engagement** section | Automated + queue |
| `articles.json` | **Latest articles** section | Automated + queue |
| `queue.json` | — staging only — | You (or manual-update workflow) |

---

## Adding content manually — two ways

### Option A: GitHub Actions form (no git clone needed)

1. Go to the repo on GitHub → **Actions** tab
2. Select **"Manual content update"** in the left sidebar
3. Click **"Run workflow"** → fill in the form fields → **Run workflow**
4. A pull request will appear within ~1 minute — review and merge to publish

### Option B: Edit `queue.json` directly

Add an object to `data/queue.json`, commit and push (or edit via the GitHub web editor).
The item will be picked up and routed on the next bi-weekly run **or** the next time you trigger the manual-update workflow.

---

## queue.json schema

```json
[
  {
    "target":      "news",
    "type":        "conference",
    "title":       "Panel at XYZ Conference",
    "date":        "2026-05-10",
    "description": "Short summary of the event or appearance.",
    "link":        "https://example.org/event",
    "image_url":   "https://example.org/photo.jpg",
    "image":       null
  }
]
```

### Fields

| Field | Required | Description |
|---|---|---|
| `target` | **yes** | Where the item goes: `"news"` · `"media"` · `"article"` |
| `type` | yes | Item type — see lists below |
| `title` | yes | Display title |
| `date` | yes | `YYYY-MM-DD` (or `YYYY-MM` / `YYYY` if exact date unknown) |
| `description` | no | Short plain-text description (1–2 sentences) |
| `link` | no | URL to the event page, article, or media clip |
| `image_url` | no | Public image URL — downloaded automatically to `assets/images/` |
| `image` | no | Local path if you've already committed the image (e.g. `assets/images/my-photo.png`) |

**Extra fields for `target: "media"`:**
`"outlet"` — name of the media outlet (e.g. `"BBC"`, `"Le Monde"`)

**Extra fields for `target: "article"`:**
`"source"` — name of the publication or think-tank (e.g. `"Carnegie Endowment"`)

**Extra fields for `target: "news"` with embedded video:**
`"embed"` — YouTube embed URL (e.g. `"https://www.youtube.com/embed/abc123"`)
`"embed_title"` — accessible title for the iframe

### Type values

| `target` | Common `type` values |
|---|---|
| `news` | `conference` · `publication` · `article` · `media` |
| `media` | `quote` · `interview` · `panel` · `radio` · `feature` |
| `article` | `article` · `report` · `book-chapter` |

---

## Automated discovery (bi-weekly, 1st & 15th of each month)

`scripts/update_content.py` fetches from:

| Source | Target file | What it finds |
|---|---|---|
| **OpenAlex** | `news.json` | Academic publications |
| **Semantic Scholar** | `news.json` | Additional academic works & preprints |
| **Lawfare RSS** | `news.json` + `articles.json` | Authored policy articles |
| **GDELT Doc API** | `media.json` | Press mentions (last 30 days, ~65 000 sources) |
| **queue.json** | routed by `target` | Manually staged items (cleared after processing) |

Each run opens a pull request if new content is found. Merge to publish.

---

## Running the script locally

```bash
# Preview only (no writes)
DRY_RUN=1 python scripts/update_content.py

# Full run (writes JSON files, generates .update-summary.md)
python scripts/update_content.py
```
