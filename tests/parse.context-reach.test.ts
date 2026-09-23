import { describe, expect, it } from 'vitest';

import {
  type ContextReach,
  countReachesByTool,
  extractContextReaches,
  scanContextReaches,
} from '../src/parse/index.js';
import {
  ContextReachSchema,
  type ContextReach as SchemaContextReach,
} from '../src/schemas/trace.js';

/** Build an assistant entry issuing one tool_use with arbitrary arguments. */
function toolCall(id: string, name: string, input: Record<string, unknown>): string {
  return JSON.stringify({
    type: 'assistant',
    message: { content: [{ type: 'tool_use', id, name, input }] },
  });
}

/** Build a user entry carrying the matching tool_result. `text` becomes the
 * block content; `stdout` becomes a structured `toolUseResult` instead. */
function toolResult(
  id: string,
  opts: { text?: string; stdout?: string; is_error?: boolean } = {}
): string {
  const block: Record<string, unknown> = {
    type: 'tool_result',
    tool_use_id: id,
    is_error: opts.is_error ?? false,
  };
  if (opts.text !== undefined) block.content = opts.text;
  const entry: Record<string, unknown> = {
    type: 'user',
    message: { content: [block] },
  };
  if (opts.stdout !== undefined) entry.toolUseResult = { stdout: opts.stdout, stderr: '' };
  return JSON.stringify(entry);
}

function transcript(...lines: string[]): string {
  return lines.join('\n') + '\n';
}

/** A one-call transcript: issue `name(input)`, answer it, return the reaches. */
function reachesFor(name: string, input: Record<string, unknown>, text = 'ok'): ContextReach[] {
  return extractContextReaches(transcript(toolCall('c1', name, input), toolResult('c1', { text })));
}

describe('extractContextReaches — which tools count as a reach', () => {
  it('records a Read as a path reach, normalizing the path', () => {
    expect(reachesFor('Read', { file_path: './src/a.ts' })).toEqual([
      {
        tool: 'Read',
        reach_kind: 'path',
        target: 'src/a.ts',
        tool_use_id: 'c1',
        turn_index: 0,
        result_is_error: false,
        result_chars: 2,
      },
    ]);
  });

  it('accepts the alternate `path` spelling of the Read argument', () => {
    expect(reachesFor('Read', { path: 'src/b.ts' })[0]?.target).toBe('src/b.ts');
  });

  it('prefers file_path over path when a call carries both', () => {
    expect(
      reachesFor('Read', { file_path: 'src/first.ts', path: 'src/second.ts' })[0]?.target
    ).toBe('src/first.ts');
  });

  it('records a NotebookRead as a path reach', () => {
    const reach = reachesFor('NotebookRead', { notebook_path: './nb/run.ipynb' })[0];
    expect(reach).toMatchObject({
      tool: 'NotebookRead',
      reach_kind: 'path',
      target: 'nb/run.ipynb',
    });
  });

  it('records Glob and Grep as query reaches, leaving the pattern verbatim', () => {
    expect(reachesFor('Glob', { pattern: 'src/**/*.ts' })[0]).toMatchObject({
      reach_kind: 'query',
      target: 'src/**/*.ts',
    });
    expect(reachesFor('Grep', { pattern: '\\bfoo\\b' })[0]).toMatchObject({
      reach_kind: 'query',
      target: '\\bfoo\\b',
    });
  });

  it('never records a write tool — Edit and Write are not reaches', () => {
    expect(reachesFor('Write', { file_path: 'src/a.ts', content: 'x' })).toEqual([]);
    expect(reachesFor('Edit', { file_path: 'src/a.ts', new_string: 'x' })).toEqual([]);
  });

  it('ignores a reach tool whose target argument is missing or blank', () => {
    expect(reachesFor('Read', {})).toEqual([]);
    expect(reachesFor('Read', { file_path: '   ' })).toEqual([]);
    expect(reachesFor('Grep', { pattern: 42 })).toEqual([]);
  });
});

describe('extractContextReaches — the Bash command gate', () => {
  const isReach = (command: string): boolean => reachesFor('Bash', { command }).length === 1;

  it('counts read-only context lookups', () => {
    expect(isReach('bd show mem-r6yzk')).toBe(true);
    expect(isReach('bd ready --json')).toBe(true);
    expect(isReach('git log --oneline -n 5')).toBe(true);
    expect(isReach('git show HEAD~1 -- src/a.ts')).toBe(true);
    expect(isReach('gh issue view 6051')).toBe(true);
    expect(isReach('rg loadWidget src/')).toBe(true);
    expect(isReach('cat docs/architecture-decisions.md')).toBe(true);
  });

  it('does not count build, test or mutation commands', () => {
    expect(isReach('npm run check')).toBe(false);
    expect(isReach('pytest -q')).toBe(false);
    expect(isReach('git commit -m wip')).toBe(false);
    expect(isReach('bd close mem-r6yzk')).toBe(false);
    expect(isReach('gh pr create --fill')).toBe(false);
    expect(isReach('ls -la')).toBe(false);
  });

  it('records a command matching several rules exactly once', () => {
    const reaches = reachesFor('Bash', { command: 'git log --oneline | head -n 3' });
    expect(reaches).toHaveLength(1);
    expect(reaches[0]?.target).toBe('git log --oneline | head -n 3');
  });

  it('keeps the command verbatim — a command target is never path-normalized', () => {
    expect(reachesFor('Bash', { command: 'cat ./src/a.ts' })[0]?.target).toBe('cat ./src/a.ts');
  });

  it('counts a read behind a navigation, an env assignment or an input redirect', () => {
    expect(isReach('cd src/widget && rg loadWidget')).toBe(true);
    expect(isReach('GIT_PAGER=cat git log --oneline')).toBe(true);
    expect(isReach('grep -n loadWidget < src/widget/loader.ts')).toBe(true);
    expect(isReach('cat "docs/design notes.md"')).toBe(true);
    expect(isReach('bd list --json | jq -r ".[].id" | sort | uniq -c | head -n 5')).toBe(true);
  });
});

/**
 * The adversarial negative set. Every row here is a command whose *text*
 * contains a read the old anywhere-in-the-string rules fired on, but whose
 * *execution* either writes, mutates, or runs something the rule table cannot
 * vouch for. A reach is a question the agent asked; none of these is one.
 */
describe('extractContextReaches — the Bash gate refuses commands that mutate', () => {
  const isReach = (command: string): boolean => reachesFor('Bash', { command }).length === 1;

  it('refuses a heredoc that writes a file, whatever its body quotes', () => {
    expect(isReach("cat > /tmp/notes.md <<'EOF'\ngit log --oneline\nrg loadWidget src/\nEOF")).toBe(
      false
    );
    // No redirect at all: the heredoc body is still inline data being written.
    expect(isReach("bd update mem-r6yzk --notes <<'EOF'\nsee git log --oneline\nEOF")).toBe(false);
    expect(isReach('cat <<- EOF\ngit log\nEOF')).toBe(false);
    expect(isReach('grep -q loadWidget <<< "$(cat src/a.ts)"')).toBe(false);
  });

  it('refuses any write redirect, in every spelling', () => {
    expect(isReach('git log --oneline -n 20 > /tmp/history.txt')).toBe(false);
    expect(isReach('git diff HEAD~1 >> /tmp/diffs.txt')).toBe(false);
    expect(isReach('rg loadWidget src/ 2>/dev/null')).toBe(false);
    expect(isReach('bd show x &> /tmp/out')).toBe(false);
    expect(isReach('git show HEAD >| /tmp/forced')).toBe(false);
  });

  it('refuses a pipeline whose downstream stage mutates', () => {
    expect(isReach('rg -l TODO src/ | xargs rm -f')).toBe(false);
    expect(isReach('cat src/a.ts | tee src/b.ts')).toBe(false);
    expect(isReach('bd ready --json | jq -r ".[].id" | xargs -n1 bd close')).toBe(false);
    expect(isReach('git log --format=%H | xargs -n1 git cherry-pick')).toBe(false);
  });

  it('refuses a commit or PR message body that merely quotes a read command', () => {
    expect(isReach('git commit -m "loader fix; see git log --oneline for the regression"')).toBe(
      false
    );
    expect(isReach('gh pr create --title "fix" --body "reproduce with rg loadWidget src/"')).toBe(
      false
    );
    expect(isReach("bd create -t 'run bd show mem-r6yzk before landing'")).toBe(false);
  });

  it('refuses a read used as an argument to a mutating command', () => {
    expect(isReach('bd update synth-0001 --notes "$(git log --oneline -n 1)"')).toBe(false);
    expect(isReach('touch snapshot-`git log -1 --format=%h`.txt')).toBe(false);
    expect(isReach('git commit -m "$(cat /tmp/msg.txt)"')).toBe(false);
    expect(isReach('gh issue comment 42 --body "$(bd show mem-r6yzk)"')).toBe(false);
  });

  it('refuses a build pipeline even when a read command filters its output', () => {
    expect(isReach('npm run build | grep -i error')).toBe(false);
    expect(isReach('pytest -q | tail -n 20')).toBe(false);
    expect(isReach('cargo test 2>&1 | rg FAILED')).toBe(false);
  });

  it('refuses a stage the frozen table cannot vouch for — the gate fails closed', () => {
    expect(isReach('git log --oneline | ./scripts/rewrite-history.sh')).toBe(false);
    expect(isReach('rg -l TODO src/ | sed -i "s/TODO/DONE/"')).toBe(false);
    expect(isReach("cat src/a.ts | python -c \"import sys; open('b','w')\"")).toBe(false);
  });

  it('refuses an allowlisted filter invoked with a flag that writes a file', () => {
    expect(isReach('git log --format=%H | sort -o /tmp/hashes.txt')).toBe(false);
    expect(isReach('git log --format=%H | sort --output=/tmp/hashes.txt')).toBe(false);
    expect(isReach('cat /tmp/in.txt | uniq /tmp/in.txt /tmp/out.txt')).toBe(false);
  });

  it('refuses a read that follows a mutation in the same command list', () => {
    expect(isReach('bd close mem-r6yzk && bd ready')).toBe(false);
    expect(isReach('git add -A && git log --oneline -n 1')).toBe(false);
    expect(isReach('(cd src && rm -rf build); cat src/a.ts')).toBe(false);
  });

  it('refuses a read command that is only ever a quoted string', () => {
    expect(isReach('echo "git log --oneline"')).toBe(false);
    expect(isReach("printf '%s\\n' 'rg loadWidget src/'")).toBe(false);
  });
});

describe('extractContextReaches — pairing, results and ordering', () => {
  it('carries the result error flag and the result size', () => {
    const reaches = extractContextReaches(
      transcript(
        toolCall('c1', 'Read', { file_path: 'src/gone.ts' }),
        toolResult('c1', { text: 'File does not exist.', is_error: true })
      )
    );
    expect(reaches[0]).toMatchObject({ result_is_error: true, result_chars: 20 });
  });

  it('measures a structured toolUseResult (stdout + stderr) rather than the block', () => {
    const reaches = extractContextReaches(
      transcript(
        toolCall('c1', 'Bash', { command: 'bd show x' }),
        toolResult('c1', { stdout: 'abcd' })
      )
    );
    // `${stdout}\n${stderr}` — four characters plus the joining newline.
    expect(reaches[0]?.result_chars).toBe(5);
  });

  it('drops a call whose result never arrives rather than reporting an empty one', () => {
    const reaches = extractContextReaches(
      transcript(
        toolCall('c1', 'Read', { file_path: 'src/a.ts' }),
        toolResult('c1', { text: 'ok' }),
        toolCall('c2', 'Read', { file_path: 'src/truncated.ts' })
      )
    );
    expect(reaches.map(r => r.tool_use_id)).toEqual(['c1']);
  });

  it('indexes turns over user + assistant entries only', () => {
    const reaches = extractContextReaches(
      transcript(
        JSON.stringify({ type: 'user', message: { content: 'the prompt' } }),
        JSON.stringify({ type: 'summary', summary: 'not a turn' }),
        toolCall('c1', 'Read', { file_path: 'src/a.ts' }),
        toolResult('c1', { text: 'ok' })
      )
    );
    // turn 0 = the prompt, turn 1 = the assistant that issued the call.
    expect(reaches[0]?.turn_index).toBe(1);
  });

  it('gives every call in one assistant turn the same turn index', () => {
    const reaches = extractContextReaches(
      transcript(
        JSON.stringify({
          type: 'assistant',
          message: {
            content: [
              { type: 'tool_use', id: 'c1', name: 'Read', input: { file_path: 'src/a.ts' } },
              { type: 'tool_use', id: 'c2', name: 'Grep', input: { pattern: 'x' } },
            ],
          },
        }),
        toolResult('c1', { text: 'ok' }),
        toolResult('c2', { text: 'ok' })
      )
    );
    expect(reaches.map(r => r.turn_index)).toEqual([0, 0]);
  });

  it('skips unparseable and blank lines without losing the surrounding reaches', () => {
    const reaches = extractContextReaches(
      transcript(
        toolCall('c1', 'Read', { file_path: 'src/a.ts' }),
        'not json at all',
        '',
        toolResult('c1', { text: 'ok' })
      )
    );
    expect(reaches).toHaveLength(1);
  });

  it('accepts a line iterable as well as full text', () => {
    const lines = [
      toolCall('c1', 'Read', { file_path: 'src/a.ts' }),
      toolResult('c1', { text: 'ok' }),
    ];
    expect(extractContextReaches(lines)).toEqual(extractContextReaches(transcript(...lines)));
  });
});

describe('scanContextReaches — the parsed-entry denominator', () => {
  it('reports zero entries for input that is not transcript JSONL', () => {
    expect(scanContextReaches('src/a.ts(12,5): error TS2345: nope\n')).toEqual({
      entries_parsed: 0,
      reaches: [],
    });
  });

  it('distinguishes a real session with no reaches from a non-transcript', () => {
    const scan = scanContextReaches(
      transcript(
        toolCall('c1', 'Bash', { command: 'npm run build' }),
        toolResult('c1', { stdout: 'built' })
      )
    );
    expect(scan.entries_parsed).toBe(2);
    expect(scan.reaches).toEqual([]);
  });

  it('counts non-turn entries toward entries_parsed', () => {
    const scan = scanContextReaches(transcript(JSON.stringify({ type: 'summary', summary: 's' })));
    expect(scan.entries_parsed).toBe(1);
  });
});

describe('countReachesByTool', () => {
  it('tallies per tool and returns {} for no reaches', () => {
    const reaches = extractContextReaches(
      transcript(
        toolCall('c1', 'Read', { file_path: 'src/a.ts' }),
        toolResult('c1', { text: 'ok' }),
        toolCall('c2', 'Read', { file_path: 'src/b.ts' }),
        toolResult('c2', { text: 'ok' }),
        toolCall('c3', 'Grep', { pattern: 'x' }),
        toolResult('c3', { text: 'ok' })
      )
    );
    expect(countReachesByTool(reaches)).toEqual({ Read: 2, Grep: 1 });
    expect(countReachesByTool([])).toEqual({});
  });
});

describe('ContextReachSchema', () => {
  it('validates every reach the extractor produces', () => {
    const reaches = extractContextReaches(
      transcript(
        toolCall('c1', 'Read', { file_path: './src/a.ts' }),
        toolResult('c1', { text: 'ok' }),
        toolCall('c2', 'Bash', { command: 'bd show x' }),
        toolResult('c2', { stdout: 'y' })
      )
    );
    expect(reaches).toHaveLength(2);
    for (const reach of reaches) expect(() => ContextReachSchema.parse(reach)).not.toThrow();
  });

  it('is structurally identical to the extractor interface', () => {
    // Assign both ways: either field drifting breaks the typecheck, which is the
    // point — the schema and the extractor's interface are one shape.
    const fromExtractor: ContextReach = {
      tool: 'Read',
      reach_kind: 'path',
      target: 'src/a.ts',
      tool_use_id: 'c1',
      turn_index: 0,
      result_is_error: false,
      result_chars: 2,
    };
    const fromSchema: SchemaContextReach = fromExtractor;
    const roundTripped: ContextReach = fromSchema;
    expect(roundTripped).toEqual(fromExtractor);
  });

  it('rejects a row with a negative result size', () => {
    expect(() =>
      ContextReachSchema.parse({
        tool: 'Read',
        reach_kind: 'path',
        target: 'src/a.ts',
        tool_use_id: 'c1',
        turn_index: 0,
        result_is_error: false,
        result_chars: -1,
      })
    ).toThrow();
  });
});
