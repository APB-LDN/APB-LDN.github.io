# APB-LDN.github.io

This repository powers [apb-ldn.org](https://apb-ldn.org/) and now includes lightweight serverless endpoints to keep sensitive
ORCID credentials on the server while exposing public feeds for the static site.

## Environment configuration

Set the following environment variables in your hosting platform:

| Variable | Required | Purpose |
| --- | --- | --- |
| `ORCID_CLIENT_ID` | ✅ | Public ORCID application identifier used for both OAuth flows and API calls. |
| `ORCID_CLIENT_SECRET` | ✅ | Confidential secret used when exchanging tokens; never expose this in the client. |
| `ORCID_REDIRECT_URI` | ➖ | Optional explicit redirect URI when completing the OAuth code exchange. |
| `ORCID_PEER_REVIEWS_ORCID` | ✅ | ORCID identifier whose peer-review records should be surfaced on the site. Fallbacks: `ORCID_ID` or `ORCID_PROFILE_ID`. |
| `ORCID_PEER_REVIEW_SCOPE` | ➖ | Custom scope for peer-review fetches (defaults to `/read-public`). |
| `ORCID_ALLOWED_ORIGIN` | ➖ | Restrict CORS access to the OAuth callback handler. Defaults to `*`. |
| `PEER_REVIEWS_ALLOWED_ORIGIN` | ➖ | Restrict CORS access to the peer-review feed handler. Defaults to `*`. |

## Serverless endpoints

The repository exposes two handlers compatible with Node-style serverless platforms (e.g. Vercel, Netlify, Cloudflare Workers
with Node compatibility):

- `GET/POST /oauth/orcid/callback` exchanges an authorization `code` for tokens via `https://orcid.org/oauth/token`.
- `GET /api/peer-reviews/latest` requests fresh peer-review data from ORCID using the client credentials flow and returns a
  normalized JSON payload.

Both handlers live under the `api/` directory and rely on [`undici`](https://github.com/nodejs/undici) for HTTP requests.

## Front-end data flow

- The static fallback list for peer reviews lives in `data/peer-reviews.json` and is merged client-side with the live feed.
- `assets/js/peer-reviews.js` is loaded on `index.html` and hydrates the Academic Service section by combining the live feed with
the manual fallback data.

The `<body>` element now exposes the feed and fallback URLs as data attributes so the script can work in different deployment
contexts without requiring inline secrets.

## Local development

Install dependencies once (only `undici` at the moment):

```bash
npm install
```

During development you can invoke either handler locally with Node's built-in HTTP server by wiring the exported function into
Express, Fastify, or a simple `http.createServer` shim depending on your hosting provider.
