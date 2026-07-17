// Type contract for the shared git IO in git.mjs. Hand-maintained because
// git.mjs stays plain ESM (its consumers are standalone scripts, not part of
// the TS build); this declaration lets the unit tests and type-aware lint see
// real types instead of `any`.

export function gitOut(dir: string, args: string[]): string | null;

export function git(dir: string, args: string[]): string;

export function readRemotes(dir: string): Record<string, string>;
