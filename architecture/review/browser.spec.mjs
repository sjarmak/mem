import { test, expect } from '@playwright/test';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { build } from './build.mjs';
import { fakeAgent } from './fixtures/agent-child.mjs';

test('review a changed dependency, inspect both versions and prepare a finding', async ({
  page,
}) => {
  const root = mkdtempSync(join(tmpdir(), 'review-browser-'));
  try {
    const git = (...args) => execFileSync('git', args, { cwd: root, stdio: 'pipe' });
    git('init', '-q');
    git('config', 'user.email', 'test@example.invalid');
    git('config', 'user.name', 'Test');
    mkdirSync(join(root, 'src'));
    writeFileSync(join(root, 'src/a.ts'), 'export const a = 1;');
    writeFileSync(join(root, 'src/b.ts'), 'export const b = 1;');
    writeFileSync(join(root, 'src/old.ts'), "import './b.js';");
    git('add', '.');
    git('commit', '-qm', 'base');
    rmSync(join(root, 'src/old.ts'));
    writeFileSync(
      join(root, 'src/a.ts'),
      `import './b.js';\nexport const a = '</script><script>window.injected=true</script>';`
    );
    build({ root, out: join(root, 'review') });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(pathToFileURL(join(root, 'review/index.html')).href);
    await expect(page.getByRole('heading', { name: 'Follow the change.' })).toBeVisible();
    await page.getByRole('combobox', { name: 'Show', exact: true }).selectOption('changed');
    await expect(page.locator('#counts')).toContainText('2 of 3 modules');
    await page.getByRole('button', { name: 'a.ts changed', exact: true }).click();
    const evidence = page.getByRole('region', { name: 'Source evidence' });
    await expect(evidence.getByRole('heading', { name: 'Imports from here (1)' })).toBeVisible();
    await evidence.getByRole('button', { name: 'L1', exact: true }).click();
    await expect(page.getByLabel('Captured source code')).toContainText("import './b.js'");
    await expect(page.getByLabel('Captured source code')).toContainText('</script>');
    expect(await page.evaluate(() => window.injected)).toBeUndefined();
    await page.getByRole('combobox', { name: 'Version', exact: true }).selectOption('baseline');
    await expect(page.getByLabel('Captured source code')).toContainText('export const a = 1');
    await expect(page.getByLabel('Captured source code')).not.toContainText('import');
    await page.getByLabel('Concern or requested change').fill('Why was this dependency added?');
    await page.getByRole('button', { name: 'Prepare review packet' }).click();
    const packet = JSON.parse(await page.getByLabel('Review packet', { exact: true }).inputValue());
    expect(packet.concern).toBe('Why was this dependency added?');
    expect(packet.dependencies[0].change).toBe('added');
    expect(packet.current.fingerprint).toMatch(/^[a-f0-9]{64}$/);
    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download packet' }).click();
    const download = await downloadEvent;
    expect(download.suggestedFilename()).toBe('review-finding.json');
    await page.getByLabel('Concern or requested change').fill('Changed my question');
    await expect(page.getByRole('button', { name: 'Copy packet' })).toBeDisabled();
    await page.getByRole('button', { name: 'old.ts removed', exact: true }).focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('combobox', { name: 'Version', exact: true })).toHaveValue(
      'baseline'
    );
    await expect(page.getByLabel('Captured source code')).toContainText("import './b.js'");
    await page.getByRole('combobox', { name: 'Version', exact: true }).selectOption('current');
    await expect(page.getByLabel('Captured source code')).toContainText(
      'does not exist in this version'
    );
    await page.getByLabel('Find a module').fill('not-found');
    await expect(
      page.getByText('No modules match. Clear the search or change the filter.')
    ).toBeVisible();
    await page.getByRole('button', { name: 'Switch theme' }).click();
    await page.getByText(/Scan details$/).click();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true
    );
    expect(errors).toEqual([]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test.describe('review navigation regressions', () => {
  let root;
  test.beforeEach(async ({ page }) => {
    root = mkdtempSync(join(tmpdir(), 'review-navigation-'));
    const git = (...args) => execFileSync('git', args, { cwd: root, stdio: 'pipe' });
    git('init', '-q');
    git('config', 'user.email', 'test@example.invalid');
    git('config', 'user.name', 'Test');
    mkdirSync(join(root, 'src'));
    writeFileSync(join(root, 'src/a.ts'), "import './b.js'; export const a = 1;");
    writeFileSync(join(root, 'src/b.ts'), 'export const b = 1;');
    git('add', '.');
    git('commit', '-qm', 'base');
    build({ root, out: join(root, 'review') });
    await page.goto(pathToFileURL(join(root, 'review/index.html')).href);
  });
  test.afterEach(() => rmSync(root, { recursive: true, force: true }));

  test('keyboard selection retains a useful focus target', async ({ page }) => {
    const module = page
      .getByRole('complementary', { name: 'Source modules' })
      .getByRole('button')
      .first();
    await module.focus();
    await page.keyboard.press('Enter');
    await expect(module).toBeFocused();
  });

  test('draft survives following a dependency and returning', async ({ page }) => {
    const modules = page.getByRole('complementary', { name: 'Source modules' });
    await modules.getByRole('button', { name: 'a.ts unchanged', exact: true }).click();
    await page.getByLabel('Concern or requested change').fill('Why does a depend on b?');
    await page
      .getByRole('region', { name: 'Source evidence' })
      .getByRole('button', { name: /b.ts/ })
      .click();
    await modules.getByRole('button', { name: 'a.ts unchanged', exact: true }).click();
    await expect(page.getByLabel('Concern or requested change')).toHaveValue(
      'Why does a depend on b?'
    );
  });

  test('filters clear excluded selection and overview resets the filter', async ({ page }) => {
    await page
      .getByRole('complementary', { name: 'Source modules' })
      .getByRole('button')
      .first()
      .click();
    await page.getByLabel('Find a module').fill('not-found');
    await expect(page.getByRole('heading', { name: 'Architecture diff' })).toBeVisible();
    await expect(page.getByLabel('Concern or requested change')).toBeHidden();
    await expect(page.locator('#graph [role="button"]')).toHaveCount(0);
    await page.getByRole('button', { name: 'Package overview', exact: true }).click();
    await expect(page.locator('#counts')).toContainText('2 of 2 modules');
  });
  test('large dependency views stay bounded and readable at tablet width', async ({ page }) => {
    for (let i = 0; i < 30; i++)
      writeFileSync(join(root, `src/dep${i}.ts`), `export const n = ${i};`);
    writeFileSync(
      join(root, 'src/a.ts'),
      Array.from({ length: 30 }, (_, i) => `import './dep${i}.js';`).join('\n')
    );
    build({ root, out: join(root, 'review') });
    await page.setViewportSize({ width: 768, height: 900 });
    await page.reload();
    await page
      .getByRole('complementary', { name: 'Source modules' })
      .getByRole('button', { name: 'a.ts changed', exact: true })
      .click();
    const size = await page
      .locator('#graph')
      .evaluate(graph => ({ height: graph.clientHeight, content: graph.scrollHeight }));
    expect(size.height).toBeLessThanOrEqual(600);
    expect(size.content).toBeGreaterThan(size.height);
    const textSize = await page
      .locator('#graph text')
      .first()
      .evaluate(text => parseFloat(getComputedStyle(text).fontSize) * text.getScreenCTM().a);
    expect(textSize).toBeGreaterThanOrEqual(12);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true
    );
  });
});

test('live refresh preserves baseline and notes, and prepares a fresh agent request', async ({
  page,
}) => {
  const { createReviewServer } = await import('./server.mjs');
  const root = mkdtempSync(join(tmpdir(), 'review-live-browser-'));
  let launches = 0;
  const server = createReviewServer({
    root,
    agentOptions: {
      spawnProcess: (...args) => fakeAgent(launches++ === 0 ? 'success' : 'hang')(...args),
    },
  });
  try {
    const git = (...args) =>
      execFileSync('git', args, { cwd: root, encoding: 'utf8', stdio: 'pipe' }).trim();
    git('init', '-q');
    git('config', 'user.email', 'test@example.invalid');
    git('config', 'user.name', 'Test');
    mkdirSync(join(root, 'src'));
    writeFileSync(join(root, 'src/a.ts'), 'export const a = 1;');
    git('add', '.');
    git('commit', '-qm', 'base');
    const baseline = git('rev-parse', 'HEAD');
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await expect(page.getByLabel('Compare working tree against')).toHaveValue(baseline);
    await page.getByRole('button', { name: 'a.ts unchanged', exact: true }).click();
    await page.getByLabel('Concern or requested change').fill('Check the changed value');
    writeFileSync(join(root, 'src/a.ts'), 'export const a = 2;');
    git('add', '.');
    git('commit', '-qm', 'change');
    const latest = git('rev-parse', 'HEAD');
    await page.reload();
    await expect(page.getByLabel('Compare working tree against')).toHaveValue(baseline);
    writeFileSync(join(root, 'src/new.ts'), 'export const added = true;');
    await page.getByRole('button', { name: 'Refresh changes', exact: true }).click();
    await expect(page.getByLabel('Compare working tree against')).toHaveValue(baseline);
    await expect(page.getByLabel('Concern or requested change')).toHaveValue(
      'Check the changed value'
    );
    await expect(page.getByLabel('Captured source code')).toContainText('a = 2');
    await expect(page.getByRole('button', { name: 'new.ts added', exact: true })).toBeVisible();
    await page.getByText('Review with an agent', { exact: true }).click();
    await page.getByRole('button', { name: 'Prepare agent request', exact: true }).click();
    const prompt = page.getByLabel('Agent review request');
    expect(await prompt.inputValue()).toContain(baseline);
    expect(await prompt.inputValue()).toContain(root);
    expect(await prompt.inputValue()).toContain('Check the changed value');
    await page.getByLabel('Concern or requested change').fill('New concern');
    await expect(
      page.getByRole('button', { name: 'Copy agent request', exact: true })
    ).toBeDisabled();
    await page.getByLabel('Compare working tree against').selectOption(latest);
    await page.getByRole('button', { name: 'Refresh changes', exact: true }).click();
    await expect(page.getByRole('button', { name: 'a.ts unchanged', exact: true })).toBeVisible();
    await expect(page.getByLabel('Concern or requested change')).toHaveValue('New concern');
    await page.getByText('Review with an agent', { exact: true }).click();
    await page.getByRole('button', { name: 'Launch review agent', exact: true }).click();
    await expect(page.locator('#agent-run-status')).toContainText('completed:', { timeout: 10000 });
    await expect(page.getByLabel('Agent findings')).toContainText('P1: Example finding <script>');
    await expect(page.getByLabel('Agent findings')).toContainText('New concern');
    expect(await page.evaluate(() => window.injected)).toBeUndefined();
    await page.reload();
    await page.getByText('Review with an agent', { exact: true }).click();
    await expect(page.getByLabel('Agent findings')).toContainText('P1: Example finding');
    await page.getByRole('button', { name: 'Launch review agent', exact: true }).click();
    await expect(
      page.getByRole('button', { name: 'Launch review agent', exact: true })
    ).toBeDisabled();
    await page.getByRole('button', { name: 'Stop review', exact: true }).click();
    await expect(page.locator('#agent-run-status')).toContainText('cancelled:', { timeout: 10000 });
    expect(launches).toBe(2);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true
    );
    expect(errors).toEqual([]);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    rmSync(root, { recursive: true, force: true });
  }
});

test('architecture diff shows additions, removals, context and baseline evidence', async ({
  page,
}) => {
  const root = mkdtempSync(join(tmpdir(), 'architecture-diff-browser-'));
  const git = (...args) => execFileSync('git', args, { cwd: root, stdio: 'pipe' });
  try {
    git('init', '-q');
    git('config', 'user.name', 'Test');
    git('config', 'user.email', 'test@example.invalid');
    mkdirSync(join(root, 'src'));
    writeFileSync(
      join(root, 'src/a.ts'),
      "import './old.js';\nimport './context.js';\nexport const a = 1;"
    );
    for (const name of ['old', 'context', 'unrelated'])
      writeFileSync(join(root, `src/${name}.ts`), 'export {};');
    git('add', '.');
    git('commit', '-qm', 'baseline');
    rmSync(join(root, 'src/old.ts'));
    writeFileSync(join(root, 'src/new.ts'), 'export {};');
    writeFileSync(
      join(root, 'src/a.ts'),
      "import './new.js';\nimport './context.js';\nimport 'external-package';\nexport const a = 2;"
    );
    build({ root, out: join(root, 'review') });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(pathToFileURL(join(root, 'review/index.html')).href);
    await expect(page.locator('#graph-title')).toHaveText('Architecture diff');
    await expect(page.locator('#diff-counts')).toContainText(
      'Modules: +1 added · −1 removed · 1 code changed. Imports: +2 added · −1 removed.'
    );
    await expect(page.locator('#graph .node.added')).toHaveCount(2);
    await expect(page.locator('#graph .node.removed')).toHaveCount(1);
    await expect(page.locator('#graph .node.changed')).toHaveCount(1);
    await expect(page.locator('#graph .node.unchanged')).toHaveCount(1);
    await expect(page.locator('#graph .edge.added')).toHaveCount(2);
    await expect(page.locator('#graph')).toContainText('external-package');
    await expect(page.locator('#graph .edge.removed')).toHaveCount(1);
    await expect(page.locator('#graph .edge.unchanged')).toHaveCount(1);
    await expect(page.locator('#graph')).not.toContainText('unrelated');
    await page.getByText('Dependency changes (3)', { exact: true }).click();
    await expect(page.locator('#dependency-change-list')).toContainText('external-package');
    await page.locator('#dependency-change-list .removed').click();
    await expect(page.getByRole('combobox', { name: 'Version', exact: true })).toHaveValue(
      'baseline'
    );
    await expect(page.locator('#source .highlight')).toContainText("import './old.js'");
    await page.getByRole('button', { name: 'Architecture diff', exact: true }).click();
    await page.locator('#graph [data-module="src/old.ts"]').focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('combobox', { name: 'Version', exact: true })).toHaveValue(
      'baseline'
    );
    await page.getByRole('button', { name: 'Package overview', exact: true }).click();
    await expect(page.locator('#graph-title')).toHaveText('Package dependencies');
    await page.getByRole('button', { name: 'Architecture diff', exact: true }).click();
    await page.getByLabel('Find a module').fill('no-such-module');
    await expect(page.locator('#graph')).toContainText('No architectural changes match');
    await page.getByLabel('Find a module').fill('');
    for (const width of [375, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true
      );
    }
    await page.screenshot({ path: '/tmp/architecture-diff-visual.png' });
    await page.getByRole('button', { name: 'Switch theme' }).click();
    await expect(page.locator('body')).toHaveCSS('background-color', 'oklch(0.2 0.012 70)');
    await expect(page.locator('body')).toHaveCSS('color', 'oklch(0.92 0.015 85)');
    await page.screenshot({ path: '/tmp/architecture-diff-dark.png' });
    expect(errors).toEqual([]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
