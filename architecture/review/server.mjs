// Local reviewer: fixed Codex review launcher; no browser-supplied commands.
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createReviewAgent } from './agent.mjs';
import { createHash, randomBytes } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { buildReview } from './scan.mjs';
import { render } from './build.mjs';

const assets = new Map([
  ['/diff-model.js', ['./diff-model.js', 'text/javascript']],
  ['/review.js', ['./review.js', 'text/javascript']],
  ['/review.css', ['./review.css', 'text/css']],
  ['/styles.css', ['../site/styles.css', 'text/css']],
  ...[
    'hanken-grotesk-latin-wght-normal',
    'literata-latin-wght-normal',
    'literata-latin-wght-italic',
  ].map(name => [`/fonts/${name}.woff2`, [`../site/fonts/${name}.woff2`, 'font/woff2']]),
]);

export function createReviewServer({ root = '.', host = '127.0.0.1', agentOptions = {} } = {}) {
  root = resolve(root);
  const git = (...args) =>
    execFileSync('git', args, {
      cwd: root,
      encoding: 'utf8',
      maxBuffer: 1024 * 1024,
      stdio: ['ignore', 'pipe', 'pipe'],
    }).trim();
  const agent = createReviewAgent({ ...agentOptions, root });
  const agentToken = randomBytes(32).toString('hex');
  const server = createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader(
      'Content-Security-Policy',
      "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    );
    const send = (status, text, type = 'text/plain; charset=utf-8') => {
      res.writeHead(status, { 'Content-Type': type });
      res.end(text);
    };
    let url;
    try {
      url = new URL(req.url, `http://${req.headers.host}`);
    } catch {
      return send(400, 'Invalid request.');
    }
    if (
      ![host, '127.0.0.1', 'localhost'].includes(url.hostname) ||
      (req.headers['sec-fetch-site'] === 'cross-site' &&
        req.headers['sec-fetch-mode'] !== 'navigate')
    )
      return send(403, 'Use the review server address directly.');
    if (url.pathname === '/api/review-run') {
      const json = (status, data) => send(status, JSON.stringify(data), 'application/json');
      if (req.headers['x-review-token'] !== agentToken)
        return json(403, { error: 'Reload the review page to reconnect.' });
      if (req.method === 'GET') return json(200, { run: agent.get() });
      if (req.method !== 'POST') return json(405, { error: 'Unsupported method.' });
      if (req.headers.origin !== url.origin || req.headers['content-type'] !== 'application/json')
        return json(403, { error: 'Launch reviews from this page only.' });
      try {
        let body = '';
        for await (const chunk of req) {
          body += chunk;
          if (Buffer.byteLength(body) > 16384)
            return json(413, { error: 'Review request is too large.' });
        }
        let input;
        try {
          input = JSON.parse(body);
        } catch {
          return json(400, { error: 'Invalid review request.' });
        }
        if (
          !input ||
          typeof input !== 'object' ||
          typeof input.id !== 'string' ||
          !/^[a-zA-Z0-9-]{16,80}$/.test(input.id ?? '')
        )
          return json(400, { error: 'Invalid review request identifier.' });
        if (input.action === 'stop') return json(200, { run: agent.cancel(input.id) });
        if (
          input.action !== 'start' ||
          typeof input.baseline !== 'string' ||
          typeof input.fingerprint !== 'string' ||
          !/^[a-f0-9]{40,64}$/.test(input.baseline ?? '') ||
          !/^[a-f0-9]{64}$/.test(input.fingerprint ?? '') ||
          typeof input.concern !== 'string' ||
          input.concern.length > 4000 ||
          !(
            input.module === null ||
            (typeof input.module === 'string' && input.module.length <= 1000)
          )
        )
          return json(400, { error: 'Invalid review context.' });
        if (agent.get()?.id === input.id) return json(200, { run: agent.get() });
        if (agent.active())
          return json(409, {
            error: 'A review is already running. Stop it or wait for its findings.',
          });
        const report = buildReview(root, input.baseline);
        if (report.current.fingerprint !== input.fingerprint)
          return json(409, {
            error: 'Files changed since this scan. Click Refresh changes before launching.',
          });
        if (input.module && !report.graph.nodes.some(node => node.id === input.module))
          return json(400, { error: 'Selected module is not in this scan.' });
        return json(202, { run: agent.start(input) });
      } catch {
        return json(400, {
          error: 'Unable to start or stop this review. Refresh the page and try again.',
        });
      }
    }
    if (req.method !== 'GET') return send(405, 'Unsupported method.');
    if (url.pathname === '/favicon.ico') return send(204, '');
    const asset = assets.get(url.pathname);
    if (asset) return send(200, readFileSync(new URL(asset[0], import.meta.url)), asset[1]);
    if (url.pathname !== '/' && url.pathname !== '/index.html') return send(404, 'Not found.');
    const baseline = url.searchParams.get('baseline') || 'HEAD';
    const errorPage = (status, message) =>
      send(
        status,
        `<!doctype html><html lang="en"><meta charset="utf-8"><title>Review unavailable</title><link rel="stylesheet" href="/styles.css"><body><main><h1>Review unavailable</h1><p>${message}</p><p>Use your browser’s Back button to keep your selected baseline, or <a href="/">start from the latest commit</a>.</p></main></body></html>`,
        'text/html; charset=utf-8'
      );
    if (!/^[a-zA-Z0-9][a-zA-Z0-9._/@~^{}-]{0,199}$/.test(baseline))
      return errorPage(400, 'Choose a valid Git commit.');
    let pinned;
    try {
      pinned = git('rev-parse', '--verify', '--end-of-options', `${baseline}^{commit}`);
    } catch {
      return errorPage(400, 'That baseline is unavailable in this repository.');
    }
    try {
      const report = buildReview(root, pinned);
      report.limitations = report.limitations.map(text =>
        text.startsWith('This is a frozen snapshot.')
          ? 'This scan captures files at refresh time. Click Refresh changes after edits; there is no automatic watcher.'
          : text
      );
      const baselines = git('log', '-40', '--format=%H%x09%s', 'HEAD')
        .split('\n')
        .map(line => {
          const tab = line.indexOf('\t');
          return { revision: line.slice(0, tab), subject: line.slice(tab + 1) };
        });
      if (!baselines.some(b => b.revision === pinned))
        baselines.push({ revision: pinned, subject: 'Selected baseline' });
      report.live = {
        baselines,
        root,
        agentToken,
        projectId: createHash('sha256').update(root).digest('hex'),
      };
      send(200, render(report), 'text/html; charset=utf-8');
    } catch {
      errorPage(
        500,
        'The source scan failed. Check that src/ and tsconfig.json are readable and valid, then retry.'
      );
    }
  });
  server.on('close', () => agent.dispose());
  server.stopAgent = () => agent.dispose();
  return server;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({
    options: {
      root: { type: 'string', default: '.' },
      host: { type: 'string', default: '127.0.0.1' },
      port: { type: 'string', default: '4173' },
    },
  });
  const server = createReviewServer(values);
  for (const signal of ['SIGTERM', 'SIGINT'])
    process.on(signal, () => {
      server.stopAgent();
      server.closeAllConnections();
      server.close();
    });
  server.listen(Number(values.port), values.host, () =>
    console.log(`Live review: http://${values.host}:${values.port}/`)
  );
}
