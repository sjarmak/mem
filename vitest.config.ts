import { defineConfig } from 'vitest/config';

// Scope test discovery to `tests/`. Vitest's default whole-project scan walks
// generated artifact dirs — notably `.mem/` (multi-MB files from
// `mem build-store` and gate-probe/harbor runs, occasionally with sub-dirs that
// have restrictive permissions) — and crashes on `EACCES` during scandir.
// Whitelisting `tests/` means those dirs are never traversed, so no `.mem/`,
// `.gc/`, `.codex/`, or `.beads/` exclude is needed.
export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts'],
  },
});
