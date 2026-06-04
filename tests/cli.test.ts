import { describe, it, expect } from 'vitest';
import { parseArgs, registerCommand, listCommands } from '../src/cli/index.js';
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
});

describe('envelope', () => {
  it('wraps success with data', () => {
    const env = successEnvelope('version', { version: '0.1.0' });
    expect(env.ok).toBe(true);
    expect(env.cmd).toBe('version');
    expect(env.data).toEqual({ version: '0.1.0' });
  });

  it('wraps errors', () => {
    const env = errorEnvelope('query', ['boom']);
    expect(env.ok).toBe(false);
    expect(env.errors).toEqual(['boom']);
  });
});
