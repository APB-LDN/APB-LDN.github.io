const bodyDataset = document.body?.dataset || {};
const listSelector = '[data-peer-reviews-list]';

const classes = {
  listItem:
    'flex items-start gap-3 rounded-lg border border-indigo-100 bg-white/80 p-4 shadow-sm transition-shadow hover:shadow-md',
  iconWrapper: 'mt-1 inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-indigo-600 text-white',
  title: 'text-base font-semibold text-gray-900',
  meta: 'mt-1 text-sm text-gray-600',
  metaLabel: 'font-medium text-gray-700'
};

const fallbackUrl = bodyDataset.peerReviewsFallback || '/data/peer-reviews.json';
const feedUrl = bodyDataset.peerReviewsFeed || '/api/peer-reviews/latest';
const updatedTargetId = bodyDataset.peerReviewsUpdatedTarget || null;

const isIsoDate = value => typeof value === 'string' && !Number.isNaN(Date.parse(value));

function slugify(text) {
  return (text || '')
    .toString()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120);
}

function uniqueSortedYears(years) {
  return Array.from(
    new Set((Array.isArray(years) ? years : []).map(value => {
      const n = Number(value);
      return Number.isFinite(n) ? n : null;
    }).filter(value => value !== null))
  ).sort((a, b) => b - a);
}

function parseEntry(entry, defaultSource = 'manual') {
  if (!entry || typeof entry !== 'object') return null;
  const years = uniqueSortedYears(entry.years);
  const normalized = {
    id: entry.id || slugify(entry.name || entry.organization || entry.groupIds?.[0] || ''),
    name: entry.name || '',
    organization: entry.organization || '',
    role: entry.role || entry.reviewRole || '',
    years,
    lastReviewed: entry.lastReviewed || (years[0] || null),
    url: entry.url || null,
    source: entry.source || defaultSource,
    groupIds: Array.isArray(entry.groupIds) ? Array.from(new Set(entry.groupIds.filter(Boolean))) : [],
    aliases: Array.isArray(entry.aliases) ? Array.from(new Set(entry.aliases.filter(Boolean))) : [],
    putCodes: Array.isArray(entry.putCodes) ? Array.from(new Set(entry.putCodes.filter(Boolean))) : []
  };

  if (!normalized.id) {
    normalized.id = slugify(`${normalized.name}-${normalized.organization}`);
  }

  return normalized;
}

function findMatchingManual(remoteEntry, manualIndex) {
  if (!remoteEntry) return null;
  if (manualIndex.has(remoteEntry.id)) return manualIndex.get(remoteEntry.id);

  const candidates = Array.from(manualIndex.values());
  for (const candidate of candidates) {
    if (!candidate) continue;
    if (remoteEntry.name && candidate.name && remoteEntry.name.toLowerCase() === candidate.name.toLowerCase()) {
      return candidate;
    }
    const sharedGroupId = remoteEntry.groupIds?.some(id => candidate.groupIds?.includes(id));
    if (sharedGroupId) return candidate;
    const sharedAlias = remoteEntry.aliases?.some(alias => candidate.aliases?.includes(alias));
    if (sharedAlias) return candidate;
  }

  return null;
}

function mergeEntries(manualEntries, remoteEntries) {
  const manualIndex = new Map();
  for (const manual of manualEntries) {
    const parsed = parseEntry(manual, 'manual');
    if (parsed) {
      manualIndex.set(parsed.id, parsed);
    }
  }

  const merged = [];
  const consumedManual = new Set();

  for (const remote of remoteEntries) {
    const parsedRemote = parseEntry(remote, 'remote');
    if (!parsedRemote) continue;
    const manualMatch = findMatchingManual(parsedRemote, manualIndex);

    if (manualMatch) {
      consumedManual.add(manualMatch.id);
      const years = uniqueSortedYears([...manualMatch.years, ...parsedRemote.years]);
      const mergedEntry = {
        id: parsedRemote.id || manualMatch.id,
        name: parsedRemote.name || manualMatch.name,
        organization: parsedRemote.organization || manualMatch.organization,
        role: parsedRemote.role || manualMatch.role,
        years,
        lastReviewed: parsedRemote.lastReviewed || manualMatch.lastReviewed || (years[0] || null),
        url: parsedRemote.url || manualMatch.url || null,
        source: parsedRemote.source === 'remote' && manualMatch.source === 'manual' ? 'merged' : parsedRemote.source,
        groupIds: Array.from(new Set([...(manualMatch.groupIds || []), ...(parsedRemote.groupIds || [])])),
        aliases: Array.from(new Set([...(manualMatch.aliases || []), ...(parsedRemote.aliases || [])])),
        putCodes: Array.from(new Set([...(manualMatch.putCodes || []), ...(parsedRemote.putCodes || [])]))
      };
      merged.push(mergedEntry);
      continue;
    }

    merged.push(parsedRemote);
  }

  for (const [manualId, manualEntry] of manualIndex.entries()) {
    if (!consumedManual.has(manualId)) {
      merged.push(manualEntry);
    }
  }

  return merged
    .map(entry => ({
      ...entry,
      years: uniqueSortedYears(entry.years),
      lastReviewed: entry.lastReviewed || (entry.years[0] || null)
    }))
    .sort((a, b) => {
      const aYear = a.lastReviewed || 0;
      const bYear = b.lastReviewed || 0;
      if (aYear !== bYear) return bYear - aYear;
      return a.name.localeCompare(b.name);
    });
}

async function fetchJson(url) {
  if (!url) return null;
  try {
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' }
    });
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    return await response.json();
  } catch (error) {
    console.error('Failed to fetch JSON', url, error);
    return null;
  }
}

async function loadPeerReviews() {
  const [manualPayload, remotePayload] = await Promise.all([
    fetchJson(fallbackUrl),
    feedUrl ? fetchJson(feedUrl) : Promise.resolve(null)
  ]);

  const manualEntries = Array.isArray(manualPayload?.entries) ? manualPayload.entries : [];
  const remoteEntries = Array.isArray(remotePayload?.entries) ? remotePayload.entries : [];

  const merged = mergeEntries(manualEntries, remoteEntries);

  return {
    entries: merged,
    manualMeta: manualPayload?.meta || null,
    remoteMeta: remotePayload?.meta || null
  };
}

function createIcon() {
  return `
    <span class="${classes.iconWrapper}">
      <svg class="h-5 w-5" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
        <path fill-rule="evenodd" d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-7.5 9.5a.75.75 0 0 1-1.127.075l-3.5-3.5a.75.75 0 0 1 1.06-1.06l2.887 2.886 6.977-8.844a.75.75 0 0 1 1.06-.109z" clip-rule="evenodd"></path>
      </svg>
    </span>
  `;
}

function renderEntries(target, entries) {
  target.innerHTML = '';
  if (!entries.length) {
    const li = document.createElement('li');
    li.className = classes.listItem;
    li.innerHTML = `${createIcon()}<div><p class="${classes.title}">Peer review activity</p><p class="${classes.meta}">No peer review data is available at this time.</p></div>`;
    target.appendChild(li);
    return;
  }

  for (const entry of entries) {
    const li = document.createElement('li');
    li.className = classes.listItem;
    const journal = entry.name || 'Peer review';
    const org = entry.organization || '';
    const yearsText = entry.years.length ? entry.years.join(', ') : (entry.lastReviewed || '');
    const metaParts = [org ? `<span class="${classes.metaLabel}">${org}</span>` : null, yearsText || null].filter(Boolean);
    const anchorStart = entry.url ? `<a href="${entry.url}" class="text-indigo-700 hover:underline" target="_blank" rel="noopener noreferrer">` : '';
    const anchorEnd = entry.url ? '</a>' : '';

    li.innerHTML = `
      ${createIcon()}
      <div>
        <p class="${classes.title}">${anchorStart}${journal}${anchorEnd}</p>
        <p class="${classes.meta}">${metaParts.join(' · ')}</p>
      </div>
    `;

    target.appendChild(li);
  }
}

function updateMeta(remoteMeta, manualMeta) {
  if (!updatedTargetId) return;
  const target = document.getElementById(updatedTargetId);
  if (!target) return;

  const remoteDate = remoteMeta?.fetchedAt && isIsoDate(remoteMeta.fetchedAt)
    ? new Date(remoteMeta.fetchedAt)
    : null;
  const manualDate = manualMeta?.manualUpdatedAt && isIsoDate(manualMeta.manualUpdatedAt)
    ? new Date(manualMeta.manualUpdatedAt)
    : null;

  if (remoteDate) {
    target.textContent = `Last updated ${remoteDate.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}`;
    return;
  }

  if (manualDate) {
    target.textContent = `Last updated ${manualDate.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}`;
  }
}

async function initialisePeerReviews() {
  const list = document.querySelector(listSelector);
  if (!list) return;

  const placeholder = list.querySelector('[data-peer-reviews-placeholder]');
  if (placeholder) {
    placeholder.remove();
  }

  const { entries, manualMeta, remoteMeta } = await loadPeerReviews();
  renderEntries(list, entries);
  updateMeta(remoteMeta, manualMeta);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initialisePeerReviews, { once: true });
} else {
  initialisePeerReviews();
}
