import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { createReviewAgent } from './agent.mjs';
import { fakeAgent } from './fixtures/agent-child.mjs';

const input = {
  id: 'test-review-identifier',
  baseline: 'a'.repeat(40),
  fingerprint: 'b'.repeat(64),
  module: 'src/a.ts',
  concern: '$(touch should-not-exist); "quoted"',
};
async function finished(agent) {
  const until = Date.now() + 6000;
  while (agent.active() && Date.now() < until)
    await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(agent.active(), false, 'child must reach terminal state');
  return agent.get();
}
function fixture(t, mode = 'success', extra = {}) {
  const root = mkdtempSync(join(tmpdir(), 'agent-test-'));
  const agent = createReviewAgent({ root, spawnProcess: fakeAgent(mode), ...extra });
  t.after(() => {
    agent.dispose();
    rmSync(root, { recursive: true, force: true });
  });
  return agent;
}
test('fixed read-only launch, stdin context, duplicate suppression and final findings', async t => {
  let count = 0;
  const agent = fixture(t, 'success', {
    spawnProcess: fakeAgent('success', (command, args, options) => {
      count++;
      assert.equal(command, 'codex');
      assert.equal(args[args.indexOf('--sandbox') + 1], 'read-only');
      assert.ok(args.includes('approval_policy="never"'));
      assert.ok(args.includes('--ignore-user-config'));
      assert.equal(options.shell, false);
      assert.ok(!args.includes(input.concern));
      assert.ok(!Object.keys(options.env).includes('GC_AGENT'));
    }),
  });
  agent.start(input);
  agent.start(input);
  assert.throws(
    () => agent.start({ ...input, id: 'another-review-identifier' }),
    /already running/
  );
  const run = await finished(agent);
  assert.equal(run.status, 'completed');
  assert.ok(run.output.includes('quoted'));
  assert.equal(count, 1);
  assert.equal(agent.start(input), run);
});
test('cancel affects only the current run and permits a later run', async t => {
  const agent = fixture(t, 'hang');
  agent.start(input);
  assert.throws(() => agent.cancel('wrong-id'), /not the current/);
  agent.cancel(input.id);
  assert.equal((await finished(agent)).status, 'cancelled');
  agent.start({ ...input, id: 'another-review-identifier' });
  agent.dispose();
  assert.equal((await finished(agent)).status, 'cancelled');
  assert.throws(() => agent.start(input), /already used/);
});
test('timeout and nonzero exit are never reported as successful reviews', async t => {
  const timeout = fixture(t, 'hang', { timeoutMs: 100 });
  timeout.start(input);
  assert.equal((await finished(timeout)).status, 'timed_out');
  const failure = fixture(t, 'fail');
  failure.start(input);
  assert.equal((await finished(failure)).status, 'failed');
});
test('missing executable fails without crashing the service', async t => {
  const agent = fixture(t, 'success', {
    spawnProcess: (_command, _args, options) =>
      spawn('/nonexistent/review-test-codex', [], { ...options, env: {} }),
  });
  agent.start(input);
  assert.equal((await finished(agent)).status, 'failed');
});
