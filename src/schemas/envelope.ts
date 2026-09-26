/**
 * CLI JSON output envelope (ported from engram src/schemas/envelope.ts).
 * Provides consistent structure for all --json outputs. Outbound-only, so a
 * plain interface — no runtime validation needed.
 */
export interface Envelope {
  apiVersion: string;
  cmd: string;
  ok: boolean;
  data?: unknown;
  errors?: string[];
  warnings?: string[];
}

export function successEnvelope(cmd: string, data?: unknown, warnings: string[] = []): Envelope {
  return {
    apiVersion: 'v1',
    cmd,
    ok: true,
    data,
    ...(warnings.length > 0 && { warnings }),
  };
}

export function errorEnvelope(cmd: string, errors: string[]): Envelope {
  return {
    apiVersion: 'v1',
    cmd,
    ok: false,
    errors,
  };
}
