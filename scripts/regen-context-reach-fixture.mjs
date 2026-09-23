// Regenerate the mine-reaches golden fixture by running the real CLI over its
// input. Run this (`npm run build && node scripts/regen-context-reach-fixture.mjs`)
// after any change to the REACH_RULES / CONTEXT_COMMAND_RULES tables or to
// normalizePath that the TS golden test flags.
//
// The INPUT is hand-authored synthetic transcript JSONL and is never
// regenerated: bead mem-r6yzk.1 forbids committing any real transcript text, so
// the input file is the reviewed artifact and only the expected output is
// mechanically derived from it.
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';

const INPUT = 'tests/fixtures/context-reach/synthetic.input.jsonl';
const OUT = 'tests/fixtures/context-reach/synthetic.expected.json';

const stdout = execFileSync('node', ['bin/mem', 'mine-reaches', '--file', INPUT, '--json'], {
  encoding: 'utf8',
});
const env = JSON.parse(stdout);
if (!env.ok) throw new Error(`mine-reaches failed: ${JSON.stringify(env.errors)}`);

writeFileSync(OUT, `${JSON.stringify(env.data, null, 2)}\n`);
console.error(`regenerated ${OUT} (${env.data.reach_count} reaches)`);
