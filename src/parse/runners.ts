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
  { re: /\beslint\b|(?<!-)\blint\b/, name: 'eslint' },
  { re: /\b(vitest|jest)\b/, name: 'vitest' },
  { re: /\bpytest\b/, name: 'pytest' },
  { re: /\bmypy\b/, name: 'mypy' },
  { re: /\bruff\b/, name: 'ruff' },
  { re: /\bgo\s+(build|test|vet)\b/, name: 'go' },
  { re: /\bcargo\s+(build|test|check|clippy)\b/, name: 'cargo' },
  { re: /(\bgradle\b|\bgradlew\b)/, name: 'gradle' },
  {
    re: /(?:^\s*|[;&|({]\s*|\n\s*)(?:(?:then|do|time|sudo|if|while|until|!|command|builtin|exec)\s+|env(?:\s+(?:\w+=\S+|-\S+))*\s+|[A-Za-z_]\w*=\S+\s+)*(?:\S*\/)?make\b/,
    name: 'make',
  },
  { re: /\bnpm\s+(run\s+)?(test|build|check|lint|typecheck)\b/, name: 'npm' },
  { re: /\b(pnpm|yarn)\s+(run\s+)?(test|build|check|lint|typecheck)\b/, name: 'pnpm' },
];

type ShellQuote = "'" | '"' | null;

function maskQuotedLine(
  line: string,
  initialQuote: ShellQuote
): { readonly masked: string; readonly quote: ShellQuote } {
  let quote = initialQuote;
  let masked = '';
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index];
    if (quote !== null) {
      if (character === quote) quote = null;
      if (character === '\\' && quote === '"' && index + 1 < line.length) {
        masked += 'xx';
        index += 1;
      } else {
        masked += 'x';
      }
    } else if (character === "'" || character === '"') {
      quote = character;
      masked += 'x';
    } else if (character === '#' && (index === 0 || /\s/.test(line[index - 1]))) {
      masked += 'x'.repeat(line.length - index);
      break;
    } else if (character === '\\' && index + 1 < line.length) {
      masked += 'xx';
      index += 1;
    } else {
      masked += character;
    }
  }
  return { masked, quote };
}

function executableShellText(command: string): string {
  let heredocs: ReadonlyArray<{ readonly delimiter: string; readonly stripTabs: boolean }> = [];
  let quote: ShellQuote = null;
  const maskedLines = command.split('\n').map(line => {
    const heredoc = heredocs[0];
    if (heredoc !== undefined) {
      const candidate = heredoc.stripTabs ? line.replace(/^\t+/, '') : line;
      if (candidate === heredoc.delimiter) heredocs = heredocs.slice(1);
      return '';
    }

    const result = maskQuotedLine(line, quote);
    quote = result.quote;
    const pattern = /(?<!<)<<(\-?)(?!<)\s*(?:'([^']+)'|"([^"]+)"|((?:\\.|[^\s;&|])+))/g;
    for (const match of line.matchAll(pattern)) {
      if (!result.masked.startsWith('<<', match.index)) continue;
      heredocs = [
        ...heredocs,
        {
          delimiter: (match[2] ?? match[3] ?? match[4]).replace(/\\(.)/g, '$1'),
          stripTabs: match[1] === '-',
        },
      ];
    }
    return result.masked;
  });
  const nestedScripts = [
    ...command.matchAll(/(?:^|[;&|]\s*)(?:\S*\/)?(?:ba|z|da)?sh\s+-\w*c\w*\s+(['"])([\s\S]*?)\1/g),
  ].map(match => executableShellText(match[2]));
  return [...maskedLines, ...nestedScripts].join('\n');
}

/**
 * Name the build/test/lint runner behind a command, or null when the command is
 * not a recognized build/test/lint run. The single classification primitive:
 * callers branch on the null to decide whether to record an {@link Execution},
 * so the "is it a build command?" and "what is it?" questions can never drift
 * apart. For a wrapper (`npm run check`) the name is the wrapper (`npm`); the
 * underlying tools surface as each error's `tool` (`tsc`, `eslint`).
 */
export function matchRunner(command: string): string | null {
  const shellText = executableShellText(command);
  return (
    RUNNER_RULES.find(rule => rule.re.test(rule.name === 'make' ? shellText : command))?.name ??
    null
  );
}
