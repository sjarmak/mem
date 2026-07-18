import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';

const here = dirname(fileURLToPath(import.meta.url));
const SCRIPT = join(here, '..', 'scripts', 'verify-rig-checkouts.mjs');

const dirs: string[] = [];
afterEach(() => {
  for (const d of dirs.splice(0)) rmSync(d, { recursive: true, force: true });
});

// A bin dir that exposes `date` but deliberately NOT `git`. hostDate() shells
// `date` at module load (before main()), so the run must be able to find it to
// reach the git-on-PATH preflight -- mirroring the real repro, where date was
// present and git was not (the crash landed at the report's `git --version`).
function binDirWithDateButNoGit(): string {
  const dir = mkdtempSync(join(tmpdir(), 'verify-rig-nogit-'));
  dirs.push(dir);
  const realDate = execFileSync('which', ['date'], { encoding: 'utf8' }).trim();
  symlinkSync(realDate, join(dir, 'date'));
  return dir;
}

describe('verify-rig-checkouts.mjs — git preflight (mem-hycs9)', () => {
  it('aborts with one named git reason and zero rig rows when git is absent from PATH', () => {
    const bin = binDirWithDateButNoGit();
    const res = spawnSync(process.execPath, [SCRIPT], {
      encoding: 'utf8',
      cwd: bin, // isolate: nothing should be written before the abort
      env: { PATH: bin }, // git is unspawnable; date resolves via the symlink
    });

    // Fail-closed: non-zero exit via abort(), not a raw uncaught crash.
    expect(res.status).not.toBe(0);

    // One true sentence that names git -- the fail-closed abort() message.
    expect(res.stderr).toContain('VERIFY ABORTED');
    expect(res.stderr).toMatch(/git/i);

    // Zero rig rows: the verdict table never renders. `checkout_kind` is a
    // renderTable() header column; `gascity` is a rig row. Neither may appear.
    expect(res.stdout).not.toContain('checkout_kind');
    expect(res.stdout).not.toContain('gascity');
  });
});
