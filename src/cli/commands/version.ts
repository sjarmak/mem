import { readFileSync } from 'node:fs';
import { z } from 'zod';
import { CommandContext } from '../index.js';

const PackageSchema = z.object({ name: z.string(), version: z.string() });

export type VersionResult = z.infer<typeof PackageSchema>;

/** Reads package.json (the single source of truth) relative to this module. */
export function versionCommand(ctx: CommandContext): VersionResult {
  const pkgUrl = new URL('../../../package.json', import.meta.url);
  const result = PackageSchema.parse(JSON.parse(readFileSync(pkgUrl, 'utf8')));

  if (!ctx.options.json) {
    console.error(`${result.name} ${result.version}`);
  }

  return result;
}
