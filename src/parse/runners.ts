/**
 * Runner classification — decide whether a shell command is a build/test/lint
 * run and, if so, name the runner. This is the gate that keeps trace parsing
 * focused: only build/test/lint executions become {@link Execution} records,
 * so a transcript's `ls`, `git status`, or `cat` calls never pollute the
 * tool-outcome signal.
 *
 * Recognition is intentionally broad (go/cargo/gradle/pytest/… as well as the
 * JS toolchain) because the *outcome* (pass/fail, from the exit code) is worth
 * capturing for every build/test/lint runner. Structured `file:line` error
 * extraction is narrower — see error-extractors (TypeScript + ESLint today).
 *
 * Mechanical token matching over a known set (the deterministic, ZFC-clean half
 * of the parse stage); the engram keyword *memory-tier* classifier — the ZFC
 * violation — is deliberately not ported.
 */

/** Ordered runner matchers. First match wins, so put specific tools (`tsc`)
 * before the generic package-manager wrappers (`npm run …`). No `g` flag: these
 * are used with `.test()`, which is stateful only on global regexes. */
const RUNNER_RULES: ReadonlyArray<{ readonly re: RegExp; readonly name: string }> = [
  { re: /\btsc\b|\btypecheck\b/, name: 'tsc' },
  { re: /\beslint\b|\blint\b/, name: 'eslint' },
  { re: /\b(vitest|jest)\b/, name: 'vitest' },
  { re: /\bpytest\b/, name: 'pytest' },
  { re: /\bmypy\b/, name: 'mypy' },
  { re: /\bruff\b/, name: 'ruff' },
  { re: /\bgo\s+(build|test|vet)\b/, name: 'go' },
  { re: /\bcargo\s+(build|test|check|clippy)\b/, name: 'cargo' },
  { re: /(\bgradle\b|\bgradlew\b)/, name: 'gradle' },
  { re: /\bmake\b/, name: 'make' },
  { re: /\bnpm\s+(run\s+)?(test|build|check|lint|typecheck)\b/, name: 'npm' },
  { re: /\b(pnpm|yarn)\s+(run\s+)?(test|build|check|lint|typecheck)\b/, name: 'pnpm' },
];

/**
 * Name the build/test/lint runner behind a command, or null when the command is
 * not a recognized build/test/lint run. The single classification primitive:
 * callers branch on the null to decide whether to record an {@link Execution},
 * so the "is it a build command?" and "what is it?" questions can never drift
 * apart. For a wrapper (`npm run check`) the name is the wrapper (`npm`); the
 * underlying tools surface as each error's `tool` (`tsc`, `eslint`).
 */
export function matchRunner(command: string): string | null {
  return RUNNER_RULES.find(rule => rule.re.test(command))?.name ?? null;
}
