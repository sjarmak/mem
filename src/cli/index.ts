import { successEnvelope, errorEnvelope } from '../schemas/envelope.js';

/**
 * Minimal command-registry CLI framework, ported from engram src/cli/index.ts.
 */

export interface CliOptions {
  json: boolean;
  verbose: boolean;
  [key: string]: string | boolean | undefined;
}

export interface CommandContext {
  args: string[];
  options: CliOptions;
}

export type CommandHandler = (ctx: CommandContext) => unknown;

const commands = new Map<string, CommandHandler>();

export function registerCommand(name: string, handler: CommandHandler): void {
  commands.set(name, handler);
}

export function listCommands(): string[] {
  return [...commands.keys()].sort();
}

export function parseArgs(argv: string[]): { command: string; ctx: CommandContext } {
  const args = argv.slice(2);

  const options: CliOptions = {
    json: false,
    verbose: false,
  };

  const filtered: string[] = [];

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--json') {
      options.json = true;
    } else if (arg === '--verbose' || arg === '-v') {
      options.verbose = true;
    } else if (arg.startsWith('--')) {
      const key = arg.slice(2);
      const nextArg = args[i + 1];
      if (nextArg && !nextArg.startsWith('--')) {
        options[key] = nextArg;
        i++;
      } else {
        options[key] = true;
      }
    } else {
      filtered.push(arg);
    }
  }

  const command = filtered[0] || 'help';
  const commandArgs = filtered.slice(1);

  return {
    command,
    ctx: {
      args: commandArgs,
      options,
    },
  };
}

export async function runCli(argv: string[]): Promise<void> {
  const { command, ctx } = parseArgs(argv);

  const handler = commands.get(command);

  if (!handler) {
    const error = `Unknown command: ${command}`;

    if (ctx.options.json) {
      console.log(JSON.stringify(errorEnvelope(command, [error])));
    } else {
      console.error(`Error: ${error}`);
      console.error('Run "mem help" for usage information');
    }
    process.exit(1);
  }

  try {
    const result = await Promise.resolve(handler(ctx));

    if (ctx.options.json) {
      console.log(JSON.stringify(successEnvelope(command, result)));
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);

    if (ctx.options.json) {
      console.log(JSON.stringify(errorEnvelope(command, [message])));
    } else {
      console.error(`Error: ${message}`);
    }
    process.exit(1);
  }
}
