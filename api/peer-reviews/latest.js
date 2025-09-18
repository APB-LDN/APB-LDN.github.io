const { fetch } = require('undici');

const HTTP_OK = 200;
const DEFAULT_SCOPE = process.env.ORCID_PEER_REVIEW_SCOPE || '/read-public';
const ORCID_BASE_URL = process.env.ORCID_BASE_URL || 'https://api.orcid.org/v3.0';
const DEFAULT_ALLOWED_ORIGIN = process.env.PEER_REVIEWS_ALLOWED_ORIGIN || '*';

function sendJson(res, statusCode, payload) {
  res.statusCode = statusCode;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(payload));
}

function normaliseYear(value) {
  if (!value) return null;
  const str = String(value).trim();
  if (!/^\d{4}$/.test(str)) return null;
  const int = Number(str);
  return Number.isFinite(int) ? int : null;
}

function slugify(input) {
  return (input || '')
    .toString()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120);
}

function ensureArray(value) {
  if (Array.isArray(value)) return value;
  if (value === undefined || value === null) return [];
  return [value];
}

function collectSummaries(payload) {
  const groups = ensureArray(payload?.['peer-review-group']);
  const summaries = [];
  for (const group of groups) {
    const groupSummaries = ensureArray(group?.['peer-review-summary']);
    for (const summary of groupSummaries) {
      summaries.push({
        summary,
        group
      });
    }
  }
  return summaries;
}

function extractValue(candidate, fallback) {
  if (!candidate) return fallback;
  if (typeof candidate === 'string') return candidate || fallback;
  if (typeof candidate === 'object') {
    if ('value' in candidate && candidate.value) return candidate.value;
  }
  return fallback;
}

function normaliseSummary(record) {
  const summary = record.summary || {};
  const group = record.group || {};
  const completionYear = normaliseYear(summary?.['completion-date']?.year?.value || summary?.['completion-date']?.value);
  const containerName = extractValue(summary?.['subject-container-name'], null);
  const subjectName = extractValue(summary?.['subject-name'], null);
  const reviewGroupType = extractValue(summary?.['review-group-type'], null);
  const name = containerName || subjectName || reviewGroupType || 'Peer review';
  const id = slugify(name) || (summary?.['put-code'] ? `put-${summary['put-code']}` : undefined) || slugify(reviewGroupType) || slugify(subjectName) || slugify(containerName) || 'peer-review';
  const reviewerOrg = extractValue(summary?.['reviewer-org']?.name, extractValue(group?.['reviewer-org']?.name, null));
  const organization = reviewerOrg || extractValue(group?.['organization']?.name, null) || extractValue(summary?.['organization']?.name, null);
  const role = extractValue(summary?.['reviewer-role']?.value, extractValue(summary?.['review-type']?.value, null));
  const url = extractValue(summary?.url?.value, extractValue(summary?.['subject-url']?.value, null));
  const groupId = extractValue(group?.['group-id'], extractValue(summary?.['group-id'], null));
  const identifiers = ensureArray(summary?.['external-identifiers']?.['external-identifier']);
  const aliasValues = identifiers
    .map(identifier => extractValue(identifier?.['external-identifier-value'], null))
    .filter(Boolean);
  const putCode = summary?.['put-code'] ? String(summary['put-code']) : null;

  return {
    id,
    name,
    organization: organization || '',
    role: role || '',
    years: completionYear ? [completionYear] : [],
    lastReviewed: completionYear || null,
    url: url || null,
    source: 'orcid',
    groupIds: groupId ? [groupId] : [],
    aliases: aliasValues,
    putCodes: putCode ? [putCode] : []
  };
}

function aggregatePeerReviews(payload) {
  const aggregated = new Map();
  for (const record of collectSummaries(payload)) {
    const entry = normaliseSummary(record);
    const key = entry.groupIds[0] || entry.id;
    const existing = aggregated.get(key);
    if (!existing) {
      aggregated.set(key, entry);
      continue;
    }

    const yearSet = new Set([...(existing.years || []), ...(entry.years || [])].filter(Number.isFinite));
    const combinedYears = Array.from(yearSet).sort((a, b) => b - a);
    existing.years = combinedYears;
    existing.lastReviewed = combinedYears[0] || existing.lastReviewed || entry.lastReviewed || null;

    if (!existing.name && entry.name) existing.name = entry.name;
    if (entry.name && entry.name.length > (existing.name || '').length) existing.name = entry.name;

    if (entry.organization && (!existing.organization || entry.organization.length > existing.organization.length)) {
      existing.organization = entry.organization;
    }

    if (entry.role && !existing.role) {
      existing.role = entry.role;
    }

    if (entry.url && !existing.url) {
      existing.url = entry.url;
    }

    existing.aliases = Array.from(new Set([...(existing.aliases || []), ...(entry.aliases || [])]));
    existing.groupIds = Array.from(new Set([...(existing.groupIds || []), ...(entry.groupIds || [])].filter(Boolean)));
    existing.putCodes = Array.from(new Set([...(existing.putCodes || []), ...(entry.putCodes || [])].filter(Boolean)));
  }

  const entries = Array.from(aggregated.values()).map(item => {
    const years = Array.isArray(item.years) ? item.years.filter(Number.isFinite).sort((a, b) => b - a) : [];
    return {
      ...item,
      years,
      lastReviewed: years[0] || item.lastReviewed || null
    };
  });

  entries.sort((a, b) => {
    const aYear = a.lastReviewed || 0;
    const bYear = b.lastReviewed || 0;
    if (aYear !== bYear) return bYear - aYear;
    return a.name.localeCompare(b.name);
  });

  return entries;
}

async function fetchPeerReviews() {
  const clientId = process.env.ORCID_CLIENT_ID;
  const clientSecret = process.env.ORCID_CLIENT_SECRET;
  const orcidId = process.env.ORCID_PEER_REVIEWS_ORCID || process.env.ORCID_ID || process.env.ORCID_PROFILE_ID;

  if (!clientId || !clientSecret || !orcidId) {
    throw new Error('Missing ORCID configuration. Ensure ORCID_CLIENT_ID, ORCID_CLIENT_SECRET and ORCID_PEER_REVIEWS_ORCID are set.');
  }

  const tokenParams = new URLSearchParams({
    client_id: clientId,
    client_secret: clientSecret,
    grant_type: 'client_credentials',
    scope: DEFAULT_SCOPE
  });

  const tokenResponse = await fetch('https://orcid.org/oauth/token', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'Accept': 'application/json'
    },
    body: tokenParams.toString()
  });

  const tokenPayload = await tokenResponse.json();
  if (!tokenResponse.ok) {
    const error = tokenPayload?.error_description || tokenPayload?.error || 'ORCID token request failed.';
    throw new Error(error);
  }

  if (!tokenPayload?.access_token) {
    throw new Error('ORCID token response did not include an access token.');
  }

  const peerReviewUrl = `${ORCID_BASE_URL.replace(/\/$/, '')}/${encodeURIComponent(orcidId)}/peer-reviews`;

  const peerResponse = await fetch(peerReviewUrl, {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${tokenPayload.access_token}`,
      Accept: 'application/json'
    }
  });

  const payload = await peerResponse.json();
  if (!peerResponse.ok) {
    const error = payload?.userMessage || payload?.developerMessage || 'ORCID peer review fetch failed.';
    throw new Error(error);
  }

  return {
    meta: {
      fetchedAt: new Date().toISOString(),
      orcidId,
      totalGroups: Array.isArray(payload?.['peer-review-group']) ? payload['peer-review-group'].length : 0
    },
    entries: aggregatePeerReviews(payload)
  };
}

async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', DEFAULT_ALLOWED_ORIGIN);
  res.setHeader('Access-Control-Allow-Methods', 'GET,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,Authorization');

  if (req.method === 'OPTIONS') {
    res.statusCode = 204;
    res.end();
    return;
  }

  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET,OPTIONS');
    sendJson(res, 405, { error: 'method_not_allowed' });
    return;
  }

  try {
    const result = await fetchPeerReviews();
    sendJson(res, HTTP_OK, {
      meta: {
        ...result.meta,
        source: 'orcid'
      },
      entries: result.entries
    });
  } catch (error) {
    sendJson(res, HTTP_OK, {
      meta: {
        fetchedAt: new Date().toISOString(),
        source: 'fallback',
        error: error instanceof Error ? error.message : 'Unknown error'
      },
      entries: []
    });
  }
}

module.exports = handler;
module.exports.default = handler;
