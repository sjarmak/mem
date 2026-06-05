import { runCli, registerCommand } from './cli/index.js';
import { helpCommand } from './cli/commands/help.js';
import { versionCommand } from './cli/commands/version.js';
import { ingestBeadsCommand } from './cli/commands/ingest-beads.js';
import { queryCommand } from './cli/commands/query.js';
import { lessonsCommand } from './cli/commands/lessons.js';
import { signatureCommand } from './cli/commands/signature.js';
import { searchErrorsCommand } from './cli/commands/search-errors.js';

/** Registers all commands and runs the CLI. The bin entrypoint calls this. */
export function main(argv: string[]): Promise<void> {
  registerCommand('help', helpCommand);
  registerCommand('version', versionCommand);
  registerCommand('ingest-beads', ingestBeadsCommand);
  registerCommand('query', queryCommand);
  registerCommand('lessons', lessonsCommand);
  registerCommand('signature', signatureCommand);
  registerCommand('search-errors', searchErrorsCommand);

  return runCli(argv);
}
