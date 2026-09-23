import { request } from 'node:http';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { createReviewServer } from './server.mjs';

test('live page refresh reads edits and keeps the chosen baseline pinned', async t => {
  const root = mkdtempSync(join(tmpdir(), 'live-review-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const git = (...args) =>
    execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: 'pipe' }).trim();
  git('init', '-q');
  git('config', 'user.name', 'Test');
  git('config', 'user.email', 'test@example.invalid');
  mkdirSync(join(root, 'src'));
  writeFileSync(join(root, 'src/a.ts'), 'export const a = 1;');
  git('add', '.');
  git('commit', '-qm', 'Initial code');
  const baseline = git('rev-parse', 'HEAD');
  const server = createReviewServer({ root });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  const url = `http://127.0.0.1:${server.address().port}`;
  const report = html => JSON.parse(html.match(/id="report">([\s\S]*?)<\/script>/)[1]);
  const initial = await fetch(url);
  assert.equal(initial.status, 200);
  assert.equal(report(await initial.text()).live.baselines[0].revision, baseline);
  writeFileSync(join(root, 'src/a.ts'), 'export const a = 2;');
  git('add', '.');
  git('commit', '-qm', 'Changed code');
  const refreshed = report(await (await fetch(`${url}/?baseline=${baseline}`)).text());
  assert.equal(refreshed.baseline.revision, baseline);
  assert.equal(refreshed.graph.nodes[0].change, 'changed');
  assert.equal(refreshed.graph.nodes[0].source, 'export const a = 2;');
  assert.equal((await fetch(`${url}/?baseline=missing-ref`)).status, 400);
  assert.equal((await fetch(`${url}/?baseline=--help`)).status, 400);
  assert.equal((await fetch(`${url}/.git/config`)).status, 404);
  assert.equal((await fetch(`${url}/src/a.ts`)).status, 404);
  assert.equal((await fetch(`${url}/review.js`)).status, 200);
  assert.equal(
    await new Promise((resolve, reject) => {
      request(url, { headers: { host: 'evil.example' } }, res => {
        res.resume();
        resolve(res.statusCode);
      })
        .on('error', reject)
        .end();
    }),
    403
  );
  assert.equal(
    (await fetch(`${url}/`, { headers: { 'sec-fetch-site': 'cross-site' } })).status,
    403
  );
  assert.equal((await fetch(`${url}/`, { method: 'POST' })).status, 405);
});

test('agent API rejects forged and stale requests, deduplicates launch and cancels', async t => {
  const { fakeAgent } = await import('./fixtures/agent-child.mjs');
  const root = mkdtempSync(join(tmpdir(), 'review-api-'));
  const git = (...args) =>
    execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: 'pipe' }).trim();
  git('init', '-q');
  git('config', 'user.name', 'Test');
  git('config', 'user.email', 'test@example.invalid');
  mkdirSync(join(root, 'src'));
  writeFileSync(join(root, 'src/a.ts'), 'export const a = 1;');
  git('add', '.');
  git('commit', '-qm', 'base');
  let launches = 0;
  const server = createReviewServer({
    root,
    agentOptions: { spawnProcess: fakeAgent('hang', () => launches++) },
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    server.stopAgent();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    rmSync(root, { recursive: true, force: true });
  });
  const url = `http://127.0.0.1:${server.address().port}`;
  const html = await (await fetch(url)).text();
  const report = JSON.parse(html.match(/id="report">([\s\S]*?)<\/script>/)[1]);
  const headers = {
    'X-Review-Token': report.live.agentToken,
    'Content-Type': 'application/json',
    Origin: url,
  };
  const input = {
    action: 'start',
    id: 'api-test-launch-0001',
    baseline: report.baseline.revision,
    fingerprint: report.current.fingerprint,
    module: null,
    concern: 'Review this diff',
  };
  const post = (body, overrides = {}) =>
    fetch(url + '/api/review-run', {
      method: 'POST',
      headers: { ...headers, ...overrides },
      body: JSON.stringify(body),
    });
  assert.equal((await post(input, { 'X-Review-Token': 'forged' })).status, 403);
  assert.equal((await post(input, { Origin: 'https://evil.example' })).status, 403);
  assert.equal((await post({ ...input, concern: 7 })).status, 400);
  assert.equal((await post({ ...input, concern: 'a'.repeat(20000) })).status, 413);
  assert.equal((await post({ ...input, module: 'src/missing.ts' })).status, 400);
  writeFileSync(join(root, 'src/a.ts'), 'export const a = 2;');
  assert.equal((await post(input)).status, 409);
  assert.equal(launches, 0);
  writeFileSync(join(root, 'src/a.ts'), 'export const a = 1;');
  assert.equal((await post(input)).status, 202);
  assert.equal((await post(input)).status, 200);
  assert.equal((await post({ ...input, id: 'api-test-launch-0002' })).status, 409);
  assert.equal(launches, 1);
  assert.equal((await post({ action: 'stop', id: 'api-test-wrong-0001' })).status, 400);
  assert.equal((await post({ action: 'stop', id: input.id })).status, 200);
  let run;
  for (let i = 0; i < 100; i++) {
    run = (await (await fetch(url + '/api/review-run', { headers })).json()).run;
    if (run.status === 'cancelled') break;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  assert.equal(run.status, 'cancelled');
});
