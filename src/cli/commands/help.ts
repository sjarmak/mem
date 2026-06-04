import { CommandContext, listCommands } from '../index.js';

export interface HelpResult {
  usage: string;
  commands: string[];
}

/** Usage listing generated from the live command registry. */
export function helpCommand(ctx: CommandContext): HelpResult {
  const result: HelpResult = {
    usage: 'mem <command> [args] [--json] [--verbose]',
    commands: listCommands(),
  };

  if (!ctx.options.json) {
    console.error(`Usage: ${result.usage}`);
    console.error('');
    console.error('Commands:');
    for (const name of result.commands) {
      console.error(`  ${name}`);
    }
  }

  return result;
}
