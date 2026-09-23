import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, symlinkSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { scan, compare, snapshot, buildReview } from './scan.mjs';

const files = {
  'src/a.ts': `import type { B } from './b.js';\nexport { b } from './b.js';\nconst lazy = import('./b.js');\nconst unknown = import(name);\nimport 'node:fs';\nimport './missing.js';\n// import './fake.js'`,
  'src/b.ts': 'export interface B {}\nexport const b = 1;',
};
test('extracts real AST dependencies with evidence and explicit unknowns', () => {
  const g = scan(files);
  assert.equal(g.nodes.length, 2);
  assert.equal(g.edges.filter(e => e.target === 'src/b.ts').length, 3);
  assert.deepEqual(
    g.edges.slice(0, 3).map(e => [e.line, e.kind]),
    [
      [1, 'type'],
      [2, 'export'],
      [3, 'dynamic'],
    ]
  );
  assert.equal(g.edges.find(e => e.specifier === 'node:fs').resolution, 'external');
  assert.equal(g.edges.find(e => e.specifier === './missing.js').resolution, 'unresolved');
  assert.equal(g.edges.find(e => e.specifier === '<computed>').resolution, 'unresolved');
  assert.ok(!g.edges.some(e => e.specifier === './fake.js'));
});
test('resolves aliases and index modules and preserves unresolved aliases', () => {
  const g = scan(
    {
      'src/a.ts': "import '@/b'; import './folder'; import '@/missing';",
      'src/b.ts': '',
      'src/folder/index.ts': '',
    },
    { baseUrl: '.', paths: { '@/*': ['src/*'] } }
  );
  assert.deepEqual(
    g.edges.map(e => e.resolution),
    ['internal', 'internal', 'unresolved']
  );
  assert.deepEqual(
    g.edges.slice(0, 2).map(e => e.target),
    ['src/b.ts', 'src/folder/index.ts']
  );
});
test('diffs content and dependency identities independently of line movement', () => {
  const before = scan({ 'src/a.ts': "import './b.js'", 'src/b.ts': '', 'src/old.ts': '' });
  const after = scan({
    'src/a.ts': "\nimport './b.js'; import './new.js'",
    'src/b.ts': '',
    'src/new.ts': '',
  });
  const diff = compare(before, after);
  assert.deepEqual(
    diff.nodes.map(n => [n.id, n.change]),
    [
      ['src/a.ts', 'changed'],
      ['src/b.ts', 'unchanged'],
      ['src/new.ts', 'added'],
      ['src/old.ts', 'removed'],
    ]
  );
  assert.equal(diff.edges.filter(e => e.change === 'added').length, 1);
  assert.equal(diff.edges.filter(e => e.change === 'unchanged').length, 1);
});
test('reports syntax errors and cycles without inventing a clean result', () => {
  const g = scan({ 'src/a.ts': "import './b.js'; const x = ;", 'src/b.ts': "import './a.js'" });
  assert.ok(g.diagnostics.some(d => d.file === 'src/a.ts'));
  assert.deepEqual(g.cycles, [['src/a.ts', 'src/b.ts']]);
});
function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'architecture-review-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const git = (...args) =>
    execFileSync('git', args, { cwd: dir, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  git('init', '-q');
  git('config', 'user.name', 'Fixture');
  git('config', 'user.email', 'fixture@example.invalid');
  mkdirSync(join(dir, 'src'));
  writeFileSync(join(dir, 'src/a.ts'), 'export const a = 1;');
  writeFileSync(join(dir, 'tsconfig.json'), '{"compilerOptions":{"module":"NodeNext"}}');
  git('add', '.');
  git('commit', '-qm', 'baseline');
  return { dir, git };
}
test('snapshot compares working bytes including untracked files to pinned Git bytes', t => {
  const { dir, git } = fixture(t);
  writeFileSync(join(dir, 'src/a.ts'), 'export const a = 2;');
  writeFileSync(join(dir, 'src/new.ts'), "import './a.js'");
  writeFileSync(join(dir, 'src/ignored.py'), '# not in the TS pilot');
  symlinkSync('/etc/passwd', join(dir, 'src/link.ts'));
  const base = snapshot(dir, 'HEAD');
  const current = snapshot(dir);
  assert.equal(base.revision, git('rev-parse', 'HEAD').trim());
  assert.equal(base.files['src/a.ts'], 'export const a = 1;');
  assert.ok(current.files['src/new.ts']);
  assert.ok(!('src/link.ts' in current.files));
  assert.ok(current.exclusions.some(x => x.path === 'src/ignored.py'));
  assert.notEqual(current.fingerprint, base.fingerprint);
  assert.equal(current.dirty, true);
  const report = buildReview(dir, 'HEAD');
  assert.equal(report.graph.nodes.find(n => n.id === 'src/new.ts').change, 'added');
  assert.equal(report.current.scope, 'src/');
  assert.equal(report.metrics.coverage, null);
});
test('invalid revision and missing source root fail, rather than produce empty green reports', t => {
  const { dir } = fixture(t);
  assert.throws(() => snapshot(dir, 'no-such-ref'));
  rmSync(join(dir, 'src'), { recursive: true });
  assert.throws(() => snapshot(dir), /No source files/);
});

test('handles require, import types, type re-exports and self cycles', () => {
  const g = scan({
    'src/a.ts': `import B = require('./b'); type T = import('./b').B; export type { B } from './b'; require('./a');`,
    'src/b.ts': 'export interface B {}',
  });
  assert.deepEqual(
    g.edges.map(e => e.kind),
    ['require', 'type', 'type', 'require']
  );
  assert.deepEqual(g.cycles, [['src/a.ts']]);
});

test('builds portable assets and escapes hostile source without corrupting its bytes', async t => {
  const { build, render } = await import('./build.mjs');
  const { readFileSync, existsSync } = await import('node:fs');
  const { dir } = fixture(t);
  const source = `export const payload = '</script><script>alert(1)</script>$&';`;
  writeFileSync(join(dir, 'src/a.ts'), source);
  const out = join(dir, 'output');
  const report = build({ root: dir, out });
  const html = readFileSync(join(out, 'index.html'), 'utf8');
  assert.ok(!html.includes('<script>alert(1)'));
  const embedded = JSON.parse(html.match(/id="report">([\s\S]*?)<\/script>/)[1]);
  assert.equal(embedded.graph.nodes[0].source, source);
  assert.deepEqual(JSON.parse(readFileSync(join(out, 'report.json'), 'utf8')), report);
  for (const asset of ['review.js', 'review.css', 'styles.css', 'fonts'])
    assert.ok(existsSync(join(out, asset)));
  assert.ok(render({ separator: '\u2028\u2029' }).includes('\\u2028\\u2029'));
  const cli = execFileSync(
    process.execPath,
    [new URL('./build.mjs', import.meta.url).pathname, '--root', dir, '--out', out],
    { encoding: 'utf8' }
  );
  assert.match(cli, /1 source files/);
});

test('shows deleted imports from baseline and configuration limitations', t => {
  const { dir, git } = fixture(t);
  writeFileSync(join(dir, 'src/b.ts'), "import './a';");
  writeFileSync(join(dir, 'src/ambient.d.mts'), 'export declare const ignored: number;');
  symlinkSync('a.ts', join(dir, 'src/link.ts'));
  git('add', '.');
  git('commit', '-qm', 'dependencies');
  rmSync(join(dir, 'src/b.ts'));
  writeFileSync(join(dir, 'tsconfig.json'), '{"extends":"./base.json"}');
  const report = buildReview(dir);
  assert.equal(report.graph.nodes.find(n => n.id === 'src/b.ts').change, 'removed');
  assert.equal(report.graph.edges[0].change, 'removed');
  assert.ok(report.baseline.exclusions.some(x => x.path === 'src/link.ts'));
  assert.ok(report.current.exclusions.some(x => x.path === 'src/ambient.d.mts'));
  assert.equal(report.current.warnings.length, 1);
  writeFileSync(join(dir, 'tsconfig.json'), '{');
  assert.throws(() => snapshot(dir));
});

test('architecture landing page includes the review link only when requested', t => {
  const { dir } = fixture(t);
  const model = join(dir, 'model.json'),
    out = join(dir, 'index.html');
  writeFileSync(model, '{"views":{},"elements":{}}');
  const args = [
    new URL('../site/build-page.mjs', import.meta.url).pathname,
    '--model',
    model,
    '--figures',
    dir,
    '--out',
    out,
  ];
  execFileSync(process.execPath, args);
  assert.ok(!readFileSync(out, 'utf8').includes('>Code review</a>'));
  execFileSync(process.execPath, [...args, '--review', './review/']);
  assert.ok(readFileSync(out, 'utf8').includes('href="./review/">Code review</a>'));
});
