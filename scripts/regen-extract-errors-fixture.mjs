// Regenerate the extract-errors golden fixture by running the real CLI over its
// input. Run this (`npm run regen:fixtures`) after any change to
// failureSignature / errorClass / normalizePath that the TS golden test flags.
// The regenerated fixture is the single parity SSOT the Python consumer test
// (memory-bench/tests/test_extract_errors_cli.py) also pins to — so a hand-edit
// that diverges from the CLI output is caught by the TS golden test, and the
// reviewed fixture diff is what both languages agree on.
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';

const INPUT = 'tests/fixtures/extract-errors/polyglot.input.txt';
const OUT = 'tests/fixtures/extract-errors/polyglot.expected.json';

const stdout = execFileSync('node', ['bin/mem', 'extract-errors', '--file', INPUT, '--json'], {
  encoding: 'utf8',
});
const env = JSON.parse(stdout);
if (!env.ok) throw new Error(`extract-errors failed: ${JSON.stringify(env.errors)}`);

writeFileSync(OUT, `${JSON.stringify(env.data.errors, null, 2)}\n`);
console.error(`regenerated ${OUT} (${env.data.errors.length} rows)`);
