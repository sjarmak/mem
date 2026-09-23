import { readFileSync, writeFileSync, mkdirSync, copyFileSync, cpSync } from 'node:fs';
import { resolve, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { buildReview } from './scan.mjs';

export function render(report) {
  const data = JSON.stringify(report)
    .replace(/</g, '\\u003c')
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029');
  return readFileSync(new URL('./template.html', import.meta.url), 'utf8').replace(
    'REPORT_DATA',
    () => data
  );
}

export function build({ root = '.', baseline = 'HEAD', out = '.architecture-review' } = {}) {
  const report = buildReview(resolve(root), baseline);
  const destination = resolve(out),
    here = dirname(fileURLToPath(import.meta.url));
  mkdirSync(destination, { recursive: true });
  for (const asset of ['review.js', 'review.css', 'diff-model.js'])
    copyFileSync(join(here, asset), join(destination, asset));
  copyFileSync(join(here, '../site/styles.css'), join(destination, 'styles.css'));
  cpSync(join(here, '../site/fonts'), join(destination, 'fonts'), { recursive: true });
  writeFileSync(join(destination, 'index.html'), render(report));
  writeFileSync(join(destination, 'report.json'), `${JSON.stringify(report, null, 2)}\n`);
  return report;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({
    options: {
      root: { type: 'string', default: '.' },
      baseline: { type: 'string', default: 'HEAD' },
      out: { type: 'string', default: '.architecture-review' },
    },
  });
  const report = build(values);
  console.log(
    `Review: ${resolve(values.out, 'index.html')}\n${report.current.fileCount} source files, ${report.graph.edges.length} dependency records. Baseline ${report.baseline.revision.slice(0, 12)}.`
  );
}
