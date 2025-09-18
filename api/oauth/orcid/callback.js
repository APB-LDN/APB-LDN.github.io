const { fetch } = require('undici');

const DEFAULT_ALLOWED_ORIGIN = process.env.ORCID_ALLOWED_ORIGIN || '*';

function sendJson(res, statusCode, payload) {
  res.statusCode = statusCode;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(payload));
}

function parseQuery(req) {
  try {
    const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
    return Object.fromEntries(url.searchParams.entries());
  } catch (error) {
    return {};
  }
}

async function parseBody(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(Buffer.from(chunk));
  }
  if (!chunks.length) return {};
  const raw = Buffer.concat(chunks).toString('utf8');
  const contentType = req.headers['content-type'] || '';
  try {
    if (contentType.includes('application/json')) {
      return JSON.parse(raw);
    }
    if (contentType.includes('application/x-www-form-urlencoded')) {
      return Object.fromEntries(new URLSearchParams(raw));
    }
  } catch (error) {
    return {};
  }
  return {};
}

async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', DEFAULT_ALLOWED_ORIGIN);
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type,Authorization');

  if (req.method === 'OPTIONS') {
    res.statusCode = 204;
    res.end();
    return;
  }

  if (!['GET', 'POST'].includes(req.method)) {
    res.setHeader('Allow', 'GET,POST,OPTIONS');
    sendJson(res, 405, { error: 'method_not_allowed' });
    return;
  }

  const query = parseQuery(req);
  const body = req.method === 'POST' ? await parseBody(req) : {};
  const code = query.code || body.code;
  const redirectUri = body.redirect_uri || query.redirect_uri || process.env.ORCID_REDIRECT_URI;
  const clientId = process.env.ORCID_CLIENT_ID;
  const clientSecret = process.env.ORCID_CLIENT_SECRET;

  if (!clientId) {
    sendJson(res, 500, { error: 'configuration_error', message: 'Missing ORCID_CLIENT_ID environment variable.' });
    return;
  }

  if (!clientSecret) {
    sendJson(res, 500, { error: 'configuration_error', message: 'Missing ORCID_CLIENT_SECRET environment variable.' });
    return;
  }

  if (!code) {
    sendJson(res, 400, { error: 'invalid_request', message: 'Missing OAuth authorization code.' });
    return;
  }

  try {
    const params = new URLSearchParams({
      client_id: clientId,
      client_secret: clientSecret,
      grant_type: 'authorization_code',
      code
    });

    if (redirectUri) {
      params.set('redirect_uri', redirectUri);
    }

    const tokenResponse = await fetch('https://orcid.org/oauth/token', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
      },
      body: params.toString()
    });

    const payload = await tokenResponse.json();

    if (!tokenResponse.ok) {
      sendJson(res, tokenResponse.status, {
        error: 'token_exchange_failed',
        message: payload.error_description || 'ORCID token exchange failed.',
        details: payload
      });
      return;
    }

    sendJson(res, 200, {
      ...payload,
      receivedAt: new Date().toISOString()
    });
  } catch (error) {
    sendJson(res, 502, {
      error: 'token_exchange_error',
      message: 'Unable to complete the ORCID token exchange.',
      details: error instanceof Error ? error.message : 'Unknown error'
    });
  }
}

module.exports = handler;
module.exports.default = handler;
