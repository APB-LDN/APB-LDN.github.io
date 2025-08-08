const dns = require('node:dns');
dns.setDefaultResultOrder('ipv4first');

const { ProxyAgent } = require('undici');
const proxy = process.env.HTTPS_PROXY || process.env.HTTP_PROXY;
const dispatcher = proxy ? new ProxyAgent(proxy) : undefined;

exports.handler = async function(event) {
  const params = event.queryStringParameters || {};
  const user = params.user;
  const sortby = params.sortby || 'pubdate';
  if (!user) {
    return {
      statusCode: 400,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Content-Type': 'text/plain'
      },
      body: 'Missing required "user" parameter'
    };
  }
  const url = `https://scholar.google.com/citations?user=${encodeURIComponent(user)}&sortby=${encodeURIComponent(sortby)}`;
  try {
    const response = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; APB-LDN/1.0; +https://apb-ldn.org)'
      },
      dispatcher
    });
    if (!response.ok) {
      return {
        statusCode: response.status,
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Content-Type': 'text/plain'
        },
        body: `Upstream request failed with status ${response.status}`
      };
    }
    const html = await response.text();
    return {
      statusCode: 200,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Content-Type': 'text/html; charset=utf-8'
      },
      body: html
    };
  } catch (err) {
    return {
      statusCode: 500,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Content-Type': 'text/plain'
      },
      body: 'Error fetching Google Scholar data.'
    };
  }
};
