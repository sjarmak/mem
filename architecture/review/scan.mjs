// Observed structure only. Never loads or executes the inspected project's code.
import ts from 'typescript';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, lstatSync } from 'node:fs';
import { join, posix } from 'node:path';

const hash = value => createHash('sha256').update(value).digest('hex');
const sourcePattern = /\.(?:[cm]?[jt]s|[jt]sx)$/;
const git = (root, args) =>
  execFileSync('git', args, {
    cwd: root,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

export function snapshot(root, ref = null) {
  const revision = git(root, [
    'rev-parse',
    '--verify',
    '--end-of-options',
    `${ref ?? 'HEAD'}^{commit}`,
  ]).trim();
  const entries =
    ref === null
      ? git(root, [
          'ls-files',
          '-z',
          '--cached',
          '--others',
          '--exclude-standard',
          '--',
          'src/',
          'tsconfig.json',
        ])
          .split('\0')
          .filter(Boolean)
          .map(path => ({ path }))
      : git(root, ['ls-tree', '-rz', revision, '--', 'src/', 'tsconfig.json'])
          .split('\0')
          .filter(Boolean)
          .map(entry => {
            const [meta, path] = entry.split('\t');
            const [mode, , oid] = meta.split(' ');
            return { path, mode, oid };
          });
  const files = {},
    exclusions = [];
  let configText = '{}';
  for (const entry of entries.sort((a, b) => a.path.localeCompare(b.path))) {
    const { path } = entry;
    if (path !== 'tsconfig.json' && (!sourcePattern.test(path) || /\.d\.[cm]?ts$/.test(path))) {
      exclusions.push({ path, reason: 'Outside supported implementation extensions' });
      continue;
    }
    let text;
    if (ref !== null) {
      if (entry.mode !== '100644' && entry.mode !== '100755') {
        exclusions.push({ path, reason: 'Not a regular file' });
        continue;
      }
      text = git(root, ['cat-file', 'blob', entry.oid]);
    } else {
      try {
        const parts = path.split('/');
        if (
          parts.some((_, i) => lstatSync(join(root, ...parts.slice(0, i + 1))).isSymbolicLink())
        ) {
          exclusions.push({ path, reason: 'Symlink excluded' });
          continue;
        }
        text = readFileSync(join(root, path), 'utf8');
      } catch (error) {
        if (error.code === 'ENOENT') {
          exclusions.push({ path, reason: 'Deleted in working tree' });
          continue;
        }
        throw error;
      }
    }
    if (path === 'tsconfig.json') configText = text;
    else files[path] = text;
  }
  if (!Object.keys(files).length)
    throw new Error(`No source files in src/ at ${ref ?? 'working tree'}`);
  const parsed = ts.parseConfigFileTextToJson('tsconfig.json', configText);
  if (parsed.error)
    throw new Error(ts.flattenDiagnosticMessageText(parsed.error.messageText, '\n'));
  const config = parsed.config;
  const warnings = [];
  if (config.extends)
    warnings.push('tsconfig extends is not expanded; inherited aliases may be unresolved.');
  const fingerprint = hash(JSON.stringify({ files, configText }));
  return {
    revision,
    kind: ref === null ? 'working-tree' : 'commit',
    dirty:
      ref === null &&
      Boolean(
        git(root, [
          'status',
          '--porcelain',
          '--untracked-files=normal',
          '--',
          'src/',
          'tsconfig.json',
        ]).trim()
      ),
    fingerprint,
    scope: 'src/',
    files,
    exclusions,
    warnings,
    compilerOptions: config.compilerOptions ?? {},
  };
}

export function scan(files, compilerOptions = {}) {
  const virtualRoot = '/__review__';
  const absolute = new Map(
    Object.entries(files).map(([p, text]) => [posix.join(virtualRoot, p), text])
  );
  const converted = ts.convertCompilerOptionsFromJson(compilerOptions, virtualRoot);
  const options = {
    moduleResolution: ts.ModuleResolutionKind.NodeNext,
    module: ts.ModuleKind.NodeNext,
    allowJs: true,
    ...converted.options,
  };
  const host = {
    fileExists: p => absolute.has(p),
    readFile: p => absolute.get(p),
    directoryExists: p => [...absolute.keys()].some(f => f.startsWith(`${p}/`)),
    getCurrentDirectory: () => virtualRoot,
  };
  const nodes = [],
    edges = [],
    diagnostics = converted.errors.map(d => ({
      file: 'tsconfig.json',
      line: 1,
      message: ts.flattenDiagnosticMessageText(d.messageText, '\n'),
    }));
  for (const [id, source] of Object.entries(files).sort(([a], [b]) => a.localeCompare(b))) {
    const file = ts.createSourceFile(id, source, ts.ScriptTarget.Latest, true);
    const symbols = [];
    for (const d of file.parseDiagnostics)
      diagnostics.push({
        file: id,
        line: file.getLineAndCharacterOfPosition(d.start ?? 0).line + 1,
        message: ts.flattenDiagnosticMessageText(d.messageText, '\n'),
      });
    const lineOf = node => file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
    const record = (node, expression, kind) => {
      const literal =
        expression &&
        (ts.isStringLiteral(expression) || ts.isNoSubstitutionTemplateLiteral(expression));
      const specifier = literal ? expression.text : '<computed>';
      const resolved = literal
        ? ts.resolveModuleName(specifier, posix.join(virtualRoot, id), options, host).resolvedModule
        : undefined;
      const target =
        resolved && absolute.has(resolved.resolvedFileName)
          ? posix.relative(virtualRoot, resolved.resolvedFileName)
          : null;
      const aliased = Object.keys(compilerOptions.paths ?? {}).some(pattern =>
        pattern.includes('*')
          ? specifier.startsWith(pattern.split('*')[0]) && specifier.endsWith(pattern.split('*')[1])
          : pattern === specifier
      );
      const resolution = target
        ? 'internal'
        : !literal ||
            specifier.startsWith('.') ||
            specifier.startsWith('/') ||
            specifier.startsWith('#') ||
            aliased ||
            (compilerOptions.baseUrl && !specifier.startsWith('node:'))
          ? 'unresolved'
          : 'external';
      edges.push({ source: id, target, specifier, kind, resolution, line: lineOf(node) });
    };
    function visit(node) {
      if (ts.isImportDeclaration(node))
        record(
          node,
          node.moduleSpecifier,
          node.importClause?.isTypeOnly ||
            (node.importClause?.namedBindings &&
              ts.isNamedImports(node.importClause.namedBindings) &&
              !node.importClause.name &&
              node.importClause.namedBindings.elements.length > 0 &&
              node.importClause.namedBindings.elements.every(e => e.isTypeOnly))
            ? 'type'
            : 'import'
        );
      else if (ts.isExportDeclaration(node) && node.moduleSpecifier)
        record(node, node.moduleSpecifier, node.isTypeOnly ? 'type' : 'export');
      else if (
        ts.isImportEqualsDeclaration(node) &&
        ts.isExternalModuleReference(node.moduleReference)
      )
        record(node, node.moduleReference.expression, 'require');
      else if (ts.isImportTypeNode(node))
        record(node, ts.isLiteralTypeNode(node.argument) ? node.argument.literal : null, 'type');
      else if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword)
        record(node, node.arguments[0], 'dynamic');
      else if (
        ts.isCallExpression(node) &&
        ts.isIdentifier(node.expression) &&
        node.expression.text === 'require'
      )
        record(node, node.arguments[0], 'require');
      if (
        (ts.isFunctionDeclaration(node) ||
          ts.isClassDeclaration(node) ||
          ts.isInterfaceDeclaration(node) ||
          ts.isTypeAliasDeclaration(node)) &&
        node.name
      )
        symbols.push({ name: node.name.text, line: lineOf(node), kind: ts.SyntaxKind[node.kind] });
      ts.forEachChild(node, visit);
    }
    visit(file);
    nodes.push({ id, group: posix.dirname(id), hash: hash(source), source, symbols });
  }
  return { nodes, edges, diagnostics, cycles: findCycles(nodes, edges) };
}

// Strongly connected components, rather than enumerating exponentially many cycles.
function findCycles(nodes, edges) {
  const adjacency = new Map(nodes.map(n => [n.id, []]));
  for (const e of edges) if (e.target) adjacency.get(e.source).push(e.target);
  const indices = new Map(),
    low = new Map(),
    stack = [],
    active = new Set(),
    cycles = [];
  let next = 0;
  function visit(id) {
    indices.set(id, next);
    low.set(id, next++);
    stack.push(id);
    active.add(id);
    for (const target of adjacency.get(id)) {
      if (!indices.has(target)) {
        visit(target);
        low.set(id, Math.min(low.get(id), low.get(target)));
      } else if (active.has(target)) low.set(id, Math.min(low.get(id), indices.get(target)));
    }
    if (low.get(id) !== indices.get(id)) return;
    const component = [];
    let member;
    do {
      member = stack.pop();
      active.delete(member);
      component.push(member);
    } while (member !== id);
    if (component.length > 1 || adjacency.get(id).includes(id)) cycles.push(component.sort());
  }
  for (const n of nodes) if (!indices.has(n.id)) visit(n.id);
  return cycles.sort((a, b) => a[0].localeCompare(b[0]));
}

const edgeKey = e => JSON.stringify([e.source, e.target, e.specifier, e.kind, e.resolution]);
export function compare(before, after) {
  const oldNodes = new Map(before.nodes.map(n => [n.id, n])),
    newNodes = new Map(after.nodes.map(n => [n.id, n]));
  const nodes = [...new Set([...oldNodes.keys(), ...newNodes.keys()])].sort().map(id => {
    const old = oldNodes.get(id),
      current = newNodes.get(id);
    return {
      ...(current ?? old),
      change: !old
        ? 'added'
        : !current
          ? 'removed'
          : old.hash !== current.hash
            ? 'changed'
            : 'unchanged',
    };
  });
  const oldEdges = new Map(before.edges.map(e => [edgeKey(e), e])),
    newEdges = new Map(after.edges.map(e => [edgeKey(e), e]));
  const edges = [...new Set([...oldEdges.keys(), ...newEdges.keys()])].sort().map(key => ({
    ...(newEdges.get(key) ?? oldEdges.get(key)),
    change: !oldEdges.has(key) ? 'added' : !newEdges.has(key) ? 'removed' : 'unchanged',
  }));
  return {
    nodes,
    edges,
    cycles: after.cycles,
    diagnostics: after.diagnostics,
    baselineDiagnostics: before.diagnostics,
  };
}

export function buildReview(root, baseline = 'HEAD') {
  const before = snapshot(root, baseline),
    current = snapshot(root);
  const graph = compare(
    scan(before.files, before.compilerOptions),
    scan(current.files, current.compilerOptions)
  );
  const metadata = ({ files, compilerOptions, ...rest }) => ({
    ...rest,
    fileCount: Object.keys(files).length,
  });
  return {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    baseline: metadata(before),
    current: metadata(current),
    graph,
    baselineSources: before.files,
    metrics: { coverage: null, complexity: null, mutation: null },
    limitations: [
      'TypeScript/JavaScript implementation files under src/ only; Python, tests, declarations and other roots are excluded.',
      'Static module dependencies, not runtime calls. Bare package imports are classified external without validating installation. require() is syntactic and may be shadowed.',
      'No architectural layering policy or test metrics has been evaluated. No finding is not evidence of correctness.',
      'This is a frozen snapshot. Regenerate after edits; the page cannot observe later working-tree changes.',
    ],
  };
}
