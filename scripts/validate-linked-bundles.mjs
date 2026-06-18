// Validate how many recovered work→outcome links become REPLAYABLE bundles.
//
// A recovered outcome gives the LANDING commit (squash/direct). The gold diff is
// then `<sha>^..<sha>` with base = the parent — exact by construction, bypassing
// the replay engine that dropped hunks against a timestamp-approximate base. This
// measures, for the sound (canonical+unique) links, how many yield a usable
// base+gold-diff bundle, and how many of those carry a test (fail-to-pass oracle).
//
// Usage: node scripts/validate-linked-bundles.mjs [--rigs a,b]

import Database from 'better-sqlite3';
import { execSync } from 'child_process';
import { linkRigOutcomes } from '../dist/ingest/commitLinkage.js';
import { RIG_REPOS, DEFAULT_BRANCH } from '../dist/ingest/rig-repo-map.js';

const i = process.argv.indexOf('--rigs');
const RIGS = (i >= 0 ? process.argv[i + 1] : 'gascity_dashboard,mem,scix_experiments,gpk,migration_evals,CodeScaleBench,zeldascension,codeprobe')
  .split(',').map(s => s.trim()).filter(Boolean);

const db = new Database('.mem/store-v7-landed.db', { readonly: true });
const git = (dir, args) => execSync(`git -C ${dir} ${args}`, { stdio: ['pipe', 'pipe', 'ignore'] }).toString();
const TEST_RE = /(^|\/)(test|tests|spec|__tests__)\//i;
const TEST_FILE_RE = /\.(test|spec)\.[tj]sx?$|_test\.(go|py)$|test_.*\.py$|\.test\.|Test\.java$/;

function classify(dir, sha) {
  let parents;
  try {
    parents = git(dir, `rev-list --parents -n 1 ${sha}`).trim().split(/\s+/).slice(1);
  } catch {
    return { tier: 'missing-commit' };
  }
  if (parents.length === 0) return { tier: 'root-commit' };
  if (parents.length > 1) return { tier: 'merge-commit' }; // not a clean squash/direct landing
  let numstat;
  try {
    numstat = git(dir, `diff --numstat ${sha}^ ${sha}`).trim();
  } catch {
    return { tier: 'diff-failed' };
  }
  if (numstat === '') return { tier: 'empty-diff' };
  const files = numstat.split('\n').map(l => l.split('\t')[2]).filter(Boolean);
  const hasTest = files.some(f => TEST_RE.test(f) || TEST_FILE_RE.test(f));
  return { tier: hasTest ? 'bundle+test' : 'bundle-notest', files: files.length };
}

const TIERS = ['bundle+test', 'bundle-notest', 'empty-diff', 'merge-commit', 'root-commit', 'missing-commit', 'diff-failed'];
const grand = Object.fromEntries(TIERS.map(t => [t, 0]));

for (const rig of RIGS) {
  const repo = RIG_REPOS[rig];
  if (!repo?.dir) continue;
  const branch = repo.branch || DEFAULT_BRANCH;
  const ids = db.prepare("SELECT work_id FROM work_records WHERE rig=? AND status='closed'").all(rig).map(r => r.work_id);
  const outcomes = linkRigOutcomes(ids, repo.dir, branch);

  const tally = Object.fromEntries(TIERS.map(t => [t, 0]));
  let sound = 0;
  for (const [, { outcome, linkage }] of outcomes) {
    if (linkage === 'multiple') continue; // sound set only
    sound++;
    const { tier } = classify(repo.dir, outcome.commit_sha);
    tally[tier]++;
    grand[tier]++;
  }
  const replayable = tally['bundle+test'] + tally['bundle-notest'];
  console.log(`=== ${rig} ===  sound links=${sound}`);
  console.log(`  REPLAYABLE bundle (base=parent, gold=landing diff): ${replayable}  [${tally['bundle+test']} with test, ${tally['bundle-notest']} no test]`);
  console.log(`  rejects: empty-diff ${tally['empty-diff']} | merge ${tally['merge-commit']} | root ${tally['root-commit']} | missing ${tally['missing-commit']} | diff-failed ${tally['diff-failed']}`);
}

const gReplay = grand['bundle+test'] + grand['bundle-notest'];
console.log(`\n=== TOTAL over sound links ===`);
console.log(`  REPLAYABLE bundles: ${gReplay}  (${grand['bundle+test']} carry a test → fail-to-pass oracle possible)`);
console.log(`  rejects: empty ${grand['empty-diff']} | merge ${grand['merge-commit']} | root ${grand['root-commit']} | missing ${grand['missing-commit']} | diff-failed ${grand['diff-failed']}`);
