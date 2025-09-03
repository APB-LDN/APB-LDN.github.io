const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const URL = 'https://web.archive.org/web/20250902234456/https://scholar.google.com/citations?user=EC5PfaUAAAAJ';

function decodeHtml(str) {
  return str
    .replace(/&amp;/g, '&')
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>');
}

async function main() {
  const html = execSync(`curl -L -s ${URL}`).toString('utf8');
  const rowRegex = /<tr class="gsc_a_tr">([\s\S]*?)<\/tr>/g;
  const rows = [...html.matchAll(rowRegex)];
  const articles = rows.map(match => {
    const row = match[0];
    const titleMatch = row.match(/<a[^>]*class="gsc_a_at"[^>]*>(.*?)<\/a>/);
    const title = titleMatch ? decodeHtml(titleMatch[1]).trim() : null;
    const citeMatch = row.match(/<td class="gsc_a_c"[^>]*>(.*?)<\/td>/);
    let citations = 0;
    if (citeMatch) {
      const numMatch = citeMatch[1].match(/>(\d+)</);
      if (numMatch) citations = parseInt(numMatch[1], 10);
    }
    return { title, citations };
  }).filter(a => a.title);

  const snapshotMatch = URL.match(/web\/(\d{4})(\d{2})(\d{2})/);
  const snapshotDate = snapshotMatch ? `${snapshotMatch[1]}-${snapshotMatch[2]}-${snapshotMatch[3]}` : new Date().toISOString().split('T')[0];

  const dataPath = path.join(__dirname, '..', 'data', 'blog_posts.json');
  let data = { posts: [] };
  try {
    data = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
  } catch (err) {
    // file does not exist
  }

  data.snapshot_date = snapshotDate;
  const map = new Map(data.posts.map(p => [p.title, p]));
  for (const article of articles) {
    if (map.has(article.title)) {
      map.get(article.title).citations = article.citations;
    } else {
      map.set(article.title, article);
    }
  }
  data.posts = Array.from(map.values());

  fs.mkdirSync(path.dirname(dataPath), { recursive: true });
  fs.writeFileSync(dataPath, JSON.stringify(data, null, 2) + '\n');
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
