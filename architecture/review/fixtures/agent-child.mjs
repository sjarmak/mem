// Test-only child: no access to real agent executables or inherited credentials.
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export function fakeAgent(mode = 'success', observe = () => {}) {
  return (command, args, options) => {
    observe(command, args, options);
    return spawn(
      process.execPath,
      [fileURLToPath(import.meta.url), mode, args[args.indexOf('-o') + 1]],
      {
        ...options,
        env: {},
      }
    );
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const mode = process.argv[2],
    output = process.argv[3];
  let prompt = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', chunk => {
    prompt += chunk;
  });
  process.stdin.on('end', () => {
    console.log(JSON.stringify({ type: 'thread.started' }));
    if (mode === 'hang') {
      setInterval(() => {}, 1000);
      return;
    }
    if (mode === 'fail') process.exit(2);
    setTimeout(() => {
      writeFileSync(output, `P1: Example finding <script>window.injected=true</script>\n${prompt}`);
      console.log(JSON.stringify({ type: 'turn.completed' }));
    }, 100);
  });
}
