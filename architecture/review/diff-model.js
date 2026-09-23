// Shared by the portable page and Node tests; no DOM or repository IO.
globalThis.architectureDiff = ({ nodes, edges }) => {
  const changes = edges.filter(edge => edge.change !== 'unchanged');
  const changedIds = new Set([
    ...nodes.filter(node => node.change !== 'unchanged').map(node => node.id),
    ...changes.flatMap(edge => [edge.source, edge.target]).filter(Boolean),
  ]);
  const context = edges.filter(
    edge => edge.target && (changedIds.has(edge.source) || changedIds.has(edge.target))
  );
  const visibleIds = new Set([
    ...changedIds,
    ...context.flatMap(edge => [edge.source, edge.target]),
  ]);
  const externalChanges = changes.filter(edge => !edge.target);
  const externalId = edge =>
    `external:${JSON.stringify([edge.specifier, edge.resolution, edge.change])}`;
  const externalNodes = externalChanges
    .filter(
      (edge, index, all) => all.findIndex(other => externalId(other) === externalId(edge)) === index
    )
    .map(edge => ({
      id: externalId(edge),
      label: edge.specifier,
      change: edge.change,
      external: true,
      owners: externalChanges
        .filter(other => externalId(other) === externalId(edge))
        .map(other => other.source),
      resolution: edge.resolution,
    }));
  const countNodes = change => nodes.filter(node => node.change === change).length;
  return {
    nodes: [...nodes.filter(node => visibleIds.has(node.id)), ...externalNodes],
    edges: [...context, ...externalChanges.map(edge => ({ ...edge, target: externalId(edge) }))],
    changes,
    counts: {
      added: countNodes('added'),
      removed: countNodes('removed'),
      changed: countNodes('changed'),
      importsAdded: changes.filter(edge => edge.change === 'added').length,
      importsRemoved: changes.filter(edge => edge.change === 'removed').length,
    },
  };
};
