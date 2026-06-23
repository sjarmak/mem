// Type contract for the pure verify helpers in lib.mjs. Hand-maintained because
// lib.mjs stays plain ESM (the Step-0 verification must run with no TypeScript
// build in the loop, mirroring the Day-0 freeze); this declaration lets the unit
// tests and type-aware lint see real types instead of `any`.

export interface RemoteSlug {
  owner: string;
  name: string;
  slug: string;
}

export function parseRemoteSlug(url: string | null | undefined): RemoteSlug | null;

export function compareRemoteToSlug(
  remoteUrl: string | null | undefined,
  expectedSlug: string
): { ok: boolean; observed: string | null; expected: string; reason: string };

export function pickRemoteForSlug(
  remotes: Record<string, string>,
  expectedSlug: string
): { remote: string; url: string } | null;

export type CheckoutKind = 'primary-clone' | 'linked-worktree';

export function classifyCheckout(
  gitDir: string,
  commonDir: string
): { kind: CheckoutKind; commonDir: string; gitDir: string };

export function groupByObjectStore(
  entries: Array<{ rig: string; commonDir: string }>
): Map<string, string[]>;

export function aliasesFor(
  byStore: Map<string, string[]>,
  rig: string,
  commonDir: string
): string[];

export interface Verdict {
  rig: string;
  dir: string;
  slug: string;
  multi: boolean;
  exists: boolean;
  remote_ok: boolean | null;
  remote_observed: string | null;
  remote_name: string | null;
  checkout_kind: CheckoutKind | null;
  common_dir: string | null;
  git_dir: string | null;
  worktree: boolean;
  aliases: string[];
  ok: boolean;
  reason: string;
}

export function buildVerdict(input: {
  rig: string;
  dir: string;
  slug: string;
  multi?: boolean;
  exists: boolean;
  remotes: Record<string, string>;
  gitDir: string | null;
  commonDir: string | null;
  aliases: string[];
}): Verdict;
