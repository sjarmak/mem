import { test } from 'node:test';
import assert from 'node:assert/strict';
import './diff-model.js';
const project = globalThis.architectureDiff;
const node = (id, change = 'unchanged') => ({ id, change });
const edge = (source, target, change = 'unchanged') => ({
  source,
  target,
  change,
  specifier: target ?? 'external',
});
test('diff retains changed modules and one-hop context, excluding unrelated code', () => {
  const graph = {
    nodes: [node('a', 'changed'), node('b'), node('c'), node('d')],
    edges: [edge('a', 'b'), edge('b', 'c'), edge('c', 'd')],
  };
  const original = JSON.stringify(graph);
  const result = project(graph);
  assert.deepEqual(
    result.nodes.map(n => n.id),
    ['a', 'b']
  );
  assert.deepEqual(result.edges, [graph.edges[0]]);
  assert.equal(result.counts.changed, 1);
  assert.equal(JSON.stringify(graph), original);
});
test('dependency-only changes, deleted modules, self edges and external changes remain visible', () => {
  const graph = {
    nodes: [node('a'), node('b', 'removed'), node('c', 'added')],
    edges: [
      edge('a', 'b', 'removed'),
      edge('a', 'c', 'added'),
      edge('a', 'a', 'added'),
      edge('a', null, 'removed'),
    ],
  };
  const result = project(graph);
  assert.deepEqual(
    result.nodes.filter(n => !n.external).map(n => n.id),
    ['a', 'b', 'c']
  );
  assert.equal(result.changes.length, 4);
  assert.equal(result.edges.length, 4);
  assert.deepEqual(result.counts, {
    added: 1,
    removed: 1,
    changed: 0,
    importsAdded: 2,
    importsRemoved: 2,
  });
});
test('unchanged graph is an explicit empty diff', () => {
  assert.deepEqual(project({ nodes: [node('a')], edges: [] }).nodes, []);
});

test('shared external dependencies retain every importer for filtering', () => {
  const result = project({
    nodes: [node('a'), node('b')],
    edges: [edge('a', null, 'added'), edge('b', null, 'added')],
  });
  assert.equal(result.nodes.filter(n => n.external).length, 1);
  assert.deepEqual(result.nodes.find(n => n.external).owners, ['a', 'b']);
});
