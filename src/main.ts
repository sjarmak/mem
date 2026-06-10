import { runCli, registerCommand } from './cli/index.js';
import { helpCommand } from './cli/commands/help.js';
import { versionCommand } from './cli/commands/version.js';
import { ingestBeadsCommand } from './cli/commands/ingest-beads.js';
import { buildStoreCommand } from './cli/commands/build-store.js';
import { queryCommand } from './cli/commands/query.js';
import { lessonsCommand } from './cli/commands/lessons.js';
import { exportLessonsCommand } from './cli/commands/export-lessons.js';
import { importLessonsCommand } from './cli/commands/import-lessons.js';
import { signatureCommand } from './cli/commands/signature.js';
import { searchErrorsCommand } from './cli/commands/search-errors.js';
import { extractErrorsCommand } from './cli/commands/extract-errors.js';
import { retrieveCommand } from './cli/commands/retrieve.js';

/** Registers all commands and runs the CLI. The bin entrypoint calls this. */
export function main(argv: string[]): Promise<void> {
  registerCommand('help', helpCommand);
  registerCommand('version', versionCommand);
  registerCommand('ingest-beads', ingestBeadsCommand);
  registerCommand('build-store', buildStoreCommand);
  registerCommand('query', queryCommand);
  registerCommand('lessons', lessonsCommand);
  registerCommand('export-lessons', exportLessonsCommand);
  registerCommand('import-lessons', importLessonsCommand);
  registerCommand('signature', signatureCommand);
  registerCommand('search-errors', searchErrorsCommand);
  registerCommand('extract-errors', extractErrorsCommand);
  registerCommand('retrieve', retrieveCommand);

  return runCli(argv);
}
