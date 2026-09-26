import { describe, it, expect, vi } from 'vitest';
import { parseArgs, registerCommand, listCommands, runCli } from '../src/cli/index.js';
import { successEnvelope, errorEnvelope } from '../src/schemas/envelope.js';

describe('parseArgs', () => {
  it('defaults to help when no command is given', () => {
    const { command, ctx } = parseArgs(['node', 'mem']);
    expect(command).toBe('help');
    expect(ctx.args).toEqual([]);
    expect(ctx.options.json).toBe(false);
    expect(ctx.options.verbose).toBe(false);
  });

  it('extracts the command and positional args', () => {
    const { command, ctx } = parseArgs(['node', 'mem', 'query', 'mem-abc']);
    expect(command).toBe('query');
    expect(ctx.args).toEqual(['mem-abc']);
  });

  it('parses --json and --verbose/-v flags', () => {
    const { ctx } = parseArgs(['node', 'mem', 'help', '--json', '-v']);
    expect(ctx.options.json).toBe(true);
    expect(ctx.options.verbose).toBe(true);
  });

  it('parses --key value pairs', () => {
    const { ctx } = parseArgs(['node', 'mem', 'query', '--rig', 'gascity']);
    expect(ctx.options.rig).toBe('gascity');
  });

  it('parses bare --key as boolean true', () => {
    const { ctx } = parseArgs(['node', 'mem', 'query', '--all']);
    expect(ctx.options.all).toBe(true);
  });
});

describe('command registry', () => {
  it('lists registered commands', () => {
    registerCommand('test-cmd', () => 'ok');
    expect(listCommands()).toContain('test-cmd');
  });

  it('puts command warnings at the envelope root', async () => {
    registerCommand('warning-cmd', ctx => {
      ctx.reportWarning?.('careful');
      return { value: 1 };
    });
    const output = vi.spyOn(console, 'log').mockImplementation(() => undefined);

    await runCli(['node', 'mem', 'warning-cmd', '--json']);

    expect(JSON.parse(String(output.mock.calls[0][0]))).toEqual({
      apiVersion: 'v1',
      cmd: 'warning-cmd',
      ok: true,
      data: { value: 1 },
      warnings: ['careful'],
    });
    output.mockRestore();
  });

  it('prints command warnings in text mode', async () => {
    registerCommand('warning-text-cmd', ctx => ctx.reportWarning?.('careful'));
    const output = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    await runCli(['node', 'mem', 'warning-text-cmd']);

    expect(output).toHaveBeenCalledWith('careful');
    output.mockRestore();
  });
});

describe('envelope', () => {
  it('wraps success with data', () => {
    const env = successEnvelope('version', { version: '0.1.0' });
    expect(env.ok).toBe(true);
    expect(env.cmd).toBe('version');
    expect(env.data).toEqual({ version: '0.1.0' });
    expect(env.warnings).toBeUndefined();
  });

  it('includes warnings on a successful envelope', () => {
    const env = successEnvelope('build-store', { count: 1 }, ['prior store unreadable']);
    expect(env.ok).toBe(true);
    expect(env.warnings).toEqual(['prior store unreadable']);
  });

  it('wraps errors', () => {
    const env = errorEnvelope('query', ['boom']);
    expect(env.ok).toBe(false);
    expect(env.errors).toEqual(['boom']);
  });
});
