import { runCli, registerCommand } from './cli/index.js';
import { helpCommand } from './cli/commands/help.js';
import { versionCommand } from './cli/commands/version.js';
import { ingestBeadsCommand } from './cli/commands/ingest-beads.js';

/** Registers all commands and runs the CLI. The bin entrypoint calls this. */
export function main(argv: string[]): Promise<void> {
  registerCommand('help', helpCommand);
  registerCommand('version', versionCommand);
  registerCommand('ingest-beads', ingestBeadsCommand);

  return runCli(argv);
}
