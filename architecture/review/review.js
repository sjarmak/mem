(() => {
  'use strict';
  const report = JSON.parse(document.getElementById('report').textContent);
  const { nodes, edges, cycles, diagnostics } = report.graph;
  const diff = globalThis.architectureDiff(report.graph);
  let architectureView = 'diff';
  const byId = new Map(nodes.map(n => [n.id, n]));
  const $ = id => document.getElementById(id);
  const el = (tag, text, className) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (className) n.className = className;
    return n;
  };
  const short = value => value.slice(0, 12);
  let selected = null;
  const draftKey = `review-drafts:${report.live?.projectId ?? report.baseline.fingerprint}`;
  let saved = {};
  try {
    const parsed = JSON.parse(sessionStorage.getItem(draftKey) || '{}');
    if (parsed && typeof parsed === 'object') saved = parsed;
  } catch {
    /* storage optional */
  }
  const drafts = new Map(
    Array.isArray(saved.drafts)
      ? saved.drafts.filter(
          entry =>
            Array.isArray(entry) &&
            entry.length === 2 &&
            entry.every(value => typeof value === 'string')
        )
      : []
  );
  let highlightedLine = null;
  const cycleNodes = new Set(cycles.flat());
  const unresolvedNodes = new Set(
    edges.filter(e => e.resolution === 'unresolved' && e.change !== 'removed').map(e => e.source)
  );
  const button = (text, action) => {
    const b = el('button', text);
    b.type = 'button';
    b.addEventListener('click', action);
    return b;
  };
  try {
    document.documentElement.dataset.theme =
      localStorage.getItem('c4-theme') ||
      (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  } catch {
    /* theme remains usable without storage */
  }
  $('theme').onclick = () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem('c4-theme', next);
    } catch {
      /* optional */
    }
  };
  $('provenance').textContent =
    `Baseline ${short(report.baseline.revision)} → working tree at ${short(report.current.revision)}${report.current.dirty ? ' + local changes in scan scope' : ''} · ${new Date(report.generatedAt).toLocaleString()}`;
  $('scope-summary').textContent =
    `${report.current.fileCount} files in ${report.current.scope} · ${unresolvedNodes.size} modules with unresolved imports · ${cycles.length} cyclic groups · Scan details`;
  const scope = $('scope-details');
  scope.append(
    el(
      'p',
      `Snapshot ${report.current.fingerprint}. Baseline snapshot ${report.baseline.fingerprint}.`
    )
  );
  const limitations = el('ul');
  for (const text of [
    ...report.limitations,
    ...report.current.warnings,
    ...report.baseline.warnings,
  ])
    limitations.append(el('li', text));
  scope.append(limitations);
  for (const [title, snapshot] of [
    ['Current exclusions', report.current],
    ['Baseline exclusions', report.baseline],
  ]) {
    const d = el('details');
    d.append(el('summary', `${title} (${snapshot.exclusions.length})`));
    const list = el('ul');
    for (const x of snapshot.exclusions) list.append(el('li', `${x.path}: ${x.reason}`));
    d.append(list);
    scope.append(d);
  }
  const parseList = el('ul');
  for (const [version, list] of [
    ['Current', diagnostics],
    ['Baseline', report.graph.baselineDiagnostics],
  ])
    for (const d of list)
      parseList.append(el('li', `${version}: ${d.file}:${d.line}: ${d.message}`));
  scope.append(
    el('h3', `Parser/configuration diagnostics (${parseList.children.length})`),
    parseList
  );

  function visibleNodes() {
    const query = $('search').value.toLowerCase(),
      filter = $('filter').value;
    return nodes.filter(
      n =>
        (n.id.toLowerCase().includes(query) ||
          n.symbols.some(s => s.name.toLowerCase().includes(query))) &&
        (filter === 'all' ||
          (filter === 'changed' && n.change !== 'unchanged') ||
          (filter === 'unresolved' && unresolvedNodes.has(n.id)) ||
          (filter === 'cycles' && cycleNodes.has(n.id)))
    );
  }
  function renderList() {
    const list = $('modules');
    const focused = document.activeElement?.dataset.module;
    const scroll = $('modules').parentElement.scrollTop;
    list.replaceChildren();
    const visible = visibleNodes();
    for (const n of visible) {
      const b = button(n.id.replace(/^src\//, ''), () => select(n.id));
      b.className = 'module-button';
      b.dataset.module = n.id;
      b.setAttribute('aria-pressed', String(n.id === selected));
      b.append(
        el(
          'small',
          `${n.change}${cycleNodes.has(n.id) ? ' · cycle' : ''}${unresolvedNodes.has(n.id) ? ' · unresolved' : ''}`,
          n.change
        )
      );
      list.append(b);
    }
    if (focused)
      [...list.children].find(b => b.dataset.module === focused)?.focus({ preventScroll: true });
    $('modules').parentElement.scrollTop = scroll;
    if (!visible.length)
      list.append(el('p', 'No modules match. Clear the search or change the filter.'));
    $('counts').textContent =
      `${visible.length} of ${nodes.length} modules · ${nodes.filter(n => n.change !== 'unchanged').length} changed`;
  }
  function applyFilters() {
    if (selected && !visibleNodes().some(n => n.id === selected)) clearSelection();
    renderList();
    if (selected) drawModule();
    else drawArchitecture();
  }
  $('search').oninput = applyFilters;
  $('filter').onchange = applyFilters;

  const ns = 'http://www.w3.org/2000/svg';
  function svgElement(tag, attrs = {}, text) {
    const n = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs)) n.setAttribute(key, value);
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function diagram(items, links) {
    const columns = 2,
      width = 640,
      rowHeight = 90,
      height = Math.max(260, Math.ceil(items.length / columns) * rowHeight + 35);
    const svg = svgElement('svg', {
      viewBox: `0 0 ${width} ${height}`,
      role: 'group',
      'aria-label': $('graph-title').textContent,
    });
    const defs = svgElement('defs');
    const marker = svgElement('marker', {
      id: 'arrow',
      viewBox: '0 0 10 10',
      refX: 9,
      refY: 5,
      markerWidth: 6,
      markerHeight: 6,
      orient: 'auto-start-reverse',
    });
    marker.append(svgElement('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: 'context-stroke' }));
    defs.append(marker);
    svg.append(defs);
    const positions = new Map(
      items.map((item, i) => [
        item.id,
        { x: 20 + (i % columns) * 320, y: 20 + Math.floor(i / columns) * rowHeight },
      ])
    );
    for (const link of links) {
      const a = positions.get(link.source),
        b = positions.get(link.target);
      if (!a || !b) continue;
      const fromX = a.x + 135,
        fromY = a.y + 52,
        toX = b.x + 135,
        toY = b.y;
      const path = svgElement('path', {
        d:
          link.source === link.target
            ? `M ${a.x + 250} ${a.y + 40} C ${a.x + 310} ${a.y + 85}, ${a.x + 310} ${a.y - 25}, ${a.x + 250} ${a.y + 5}`
            : `M ${fromX + (link.change === 'removed' ? 12 : 0)} ${fromY} C ${fromX + 40} ${fromY + 28}, ${toX + 40} ${toY - 28}, ${toX + (link.change === 'removed' ? 12 : 0)} ${toY}`,
        class: `edge ${link.change ?? ''}`,
        'marker-end': 'url(#arrow)',
      });
      path.append(
        svgElement(
          'title',
          {},
          `${link.source} → ${link.target}${link.label ? `: ${link.label}` : ''}`
        )
      );
      svg.append(path);
    }
    for (const item of items) {
      const p = positions.get(item.id),
        g = svgElement('g', {
          class: `node ${item.change ?? ''} ${item.id === selected ? 'selected' : ''}`,
          role: 'button',
          tabindex: '0',
          'aria-label': item.id,
        });
      g.dataset.module = item.id;
      g.append(svgElement('rect', { x: p.x, y: p.y, width: 270, height: 52, rx: 4 }));
      g.append(
        svgElement(
          'text',
          { x: p.x + 12, y: p.y + 22 },
          item.label.length > 34 ? `${item.label.slice(0, 31)}…` : item.label
        )
      );
      g.append(svgElement('text', { x: p.x + 12, y: p.y + 40, class: 'subtext' }, item.detail));
      g.append(svgElement('title', {}, item.id));
      g.addEventListener('click', item.action);
      g.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          item.action();
        }
      });
      svg.append(g);
    }
    $('graph').replaceChildren(svg);
    if (!items.length)
      $('graph').replaceChildren(
        el(
          'p',
          architectureView === 'diff'
            ? 'No architectural changes match this scan and filter. Choose an earlier baseline to review committed changes, or edit source and refresh.'
            : 'No packages match the current filters.'
        )
      );
  }
  function drawArchitecture() {
    $('diff-view').setAttribute('aria-pressed', String(architectureView === 'diff'));
    if (architectureView !== 'diff') {
      drawOverview();
      return;
    }
    const visible = new Set(visibleNodes().map(node => node.id));
    const items = diff.nodes.filter(node =>
      node.external ? node.owners.some(owner => visible.has(owner)) : visible.has(node.id)
    );
    $('graph-title').textContent = 'Architecture diff';
    $('graph-caption').textContent =
      'Added and removed dependencies connect changed modules. Unchanged neighbors provide context. Select a module for source evidence.';
    diagram(
      items.map(node => ({
        id: node.id,
        label: node.label ?? node.id.replace(/^src\//, ''),
        change: node.change,
        detail: `${node.external ? `${node.resolution} import · ` : ''}${node.change === 'added' ? '+ added' : node.change === 'removed' ? '− removed' : node.change === 'changed' ? '● code changed' : 'unchanged context'}`,
        action: () =>
          select(node.external ? node.owners.find(owner => visible.has(owner)) : node.id),
      })),
      diff.edges
    );
  }
  const counts = diff.counts;
  $('diff-counts').textContent =
    `Modules: +${counts.added} added · −${counts.removed} removed · ${counts.changed} code changed. Imports: +${counts.importsAdded} added · −${counts.importsRemoved} removed.`;
  $('dependency-change-count').textContent = `Dependency changes (${diff.changes.length})`;
  for (const edge of diff.changes) {
    const item = button(
      `${edge.change === 'added' ? '+' : '−'} ${edge.source} → ${edge.target ?? edge.specifier} · ${edge.kind} · ${edge.resolution} · L${edge.line}`,
      () => {
        select(edge.source);
        $('source-version').value = edge.change === 'removed' ? 'baseline' : 'current';
        showSource(edge.line);
        $('source-section').scrollIntoView({ block: 'start' });
      }
    );
    item.className = `dependency-change ${edge.change}`;
    $('dependency-change-list').append(item);
  }
  if (!diff.changes.length)
    $('dependency-change-list').append(
      el('p', 'No import changes. Code-only changes are marked on their modules.')
    );
  function drawOverview() {
    $('graph-title').textContent = 'Package dependencies';
    $('graph-caption').textContent =
      'Source directory groups. Select a package to filter its modules. Arrows aggregate current and removed imports; module views label changes.';
    const visible = visibleNodes(),
      groups = [...new Set(visible.map(n => n.group))].sort();
    const links = new Map();
    for (const e of edges)
      if (e.target) {
        const source = byId.get(e.source).group,
          target = byId.get(e.target).group;
        if (source !== target && groups.includes(source) && groups.includes(target)) {
          const key = JSON.stringify([source, target]);
          if (!links.has(key)) links.set(key, { source, target, count: 0 });
          links.get(key).count++;
        }
      }
    diagram(
      groups.map(group => ({
        id: group,
        label: group,
        detail: `${visible.filter(n => n.group === group).length} modules`,
        action: () => {
          $('search').value = `${group}/`;
          renderList();
          const first = visibleNodes()[0];
          if (first) select(first.id);
        },
      })),
      [...links.values()].map(e => ({ ...e, label: `${e.count} dependency records` }))
    );
  }
  function drawModule() {
    const related = edges.filter(e => e.target && (e.source === selected || e.target === selected));
    const ids = [
      selected,
      ...[...new Set(related.flatMap(e => [e.source, e.target]))]
        .filter(id => id !== selected)
        .sort(),
    ];
    $('graph-title').textContent = 'One-hop dependencies';
    $('graph-caption').textContent =
      `${related.length} dependency records. Full paths and import lines appear in the evidence panel.`;
    diagram(
      ids.map(id => ({
        id,
        label: id.replace(/^src\//, ''),
        change: byId.get(id).change,
        detail: `${byId.get(id).change}${id === selected ? ' · selected' : ''}`,
        action: () => select(id),
      })),
      related
    );
  }
  function select(id) {
    const focused = document.activeElement;
    const graphFocus = $('graph').contains(focused);
    if (!visibleNodes().some(n => n.id === id)) {
      $('search').value = '';
      $('filter').value = 'all';
    }
    selected = id;
    highlightedLine = null;
    const node = byId.get(id);
    $('selection-title').textContent = id;
    $('selection-summary').textContent =
      `${node.change}. ${cycleNodes.has(id) ? 'In a current dependency cycle. ' : ''}Coverage, complexity and mutation: not measured.`;
    const panel = $('evidence');
    panel.replaceChildren();
    for (const [title, list] of [
      ['Imports from here', edges.filter(e => e.source === id)],
      ['Imported by', edges.filter(e => e.target === id)],
    ]) {
      panel.append(el('h3', `${title} (${list.length})`));
      if (!list.length) panel.append(el('p', 'No observed dependencies in this direction.'));
      for (const e of list) {
        const row = el('div', undefined, 'evidence-row');
        const target = title === 'Imports from here' ? e.target : e.source;
        const main = target
          ? button(target.replace(/^src\//, ''), () => select(target))
          : el('span', e.specifier);
        main.append(el('small', `${e.change} · ${e.kind} · ${e.resolution}`, e.change));
        row.append(
          main,
          button(`L${e.line}`, () => {
            if (selected !== e.source) select(e.source);
            $('source-version').value = e.change === 'removed' ? 'baseline' : 'current';
            showSource(e.line);
            $('source-section').scrollIntoView({ block: 'start' });
          })
        );
        panel.append(row);
      }
    }
    const moduleDiagnostics = diagnostics.filter(d => d.file === id);
    if (moduleDiagnostics.length) {
      panel.append(el('h3', 'Parser diagnostics'));
      for (const d of moduleDiagnostics) panel.append(el('p', `L${d.line}: ${d.message}`));
    }
    panel.append(el('h3', `Declarations (${node.symbols.length})`));
    for (const s of node.symbols)
      panel.append(
        button(`${s.name} · L${s.line}`, () => {
          $('source-version').value = node.change === 'removed' ? 'baseline' : 'current';
          showSource(s.line);
          $('source-section').scrollIntoView({ block: 'start' });
        })
      );
    $('source-section').hidden = false;
    $('handoff').hidden = false;
    $('source-version').value = node.change === 'removed' ? 'baseline' : 'current';
    $('concern').value = drafts.get(id) ?? '';
    invalidatePacket();
    renderList();
    drawModule();
    showSource();
    if (!focused.isConnected) {
      const candidates = (graphFocus ? $('graph') : $('modules')).querySelectorAll('[data-module]');
      const target = [...candidates].find(n => n.dataset.module === id) ?? $('selection-title');
      target.focus({ preventScroll: true });
    }
  }
  function showSource(line = null) {
    highlightedLine = line;
    const node = byId.get(selected),
      baseline = $('source-version').value === 'baseline';
    const source = baseline
      ? report.baselineSources[selected]
      : node.change === 'removed'
        ? undefined
        : node.source;
    $('source-title').textContent = `${selected} · ${baseline ? 'baseline' : 'current'}`;
    $('source-hash').textContent = baseline
      ? `Baseline commit ${report.baseline.revision}`
      : `File SHA-256 ${node.hash}`;
    const pre = $('source');
    pre.replaceChildren();
    if (source === undefined) {
      pre.textContent = 'This module does not exist in this version.';
      return;
    }
    source.split('\n').forEach((text, i) => {
      const row = el(
        'span',
        undefined,
        `source-line${i + 1 === highlightedLine ? ' highlight' : ''}`
      );
      row.append(el('span', `${i + 1}`, 'line-number'), document.createTextNode(text));
      pre.append(row);
    });
    if (line) pre.scrollTop = Math.max(0, (line - 4) * 20.4);
    else pre.scrollTop = 0;
  }
  $('source-version').onchange = () => showSource();
  function clearSelection() {
    selected = null;
    $('selection-title').textContent = 'Start with a module';
    $('selection-summary').textContent = 'Select a module to inspect the captured source.';
    $('evidence').replaceChildren();
    $('source-section').hidden = true;
    $('handoff').hidden = true;
    invalidatePacket();
  }
  $('diff-view').onclick = () => {
    architectureView = 'diff';
    $('search').value = '';
    $('filter').value = 'all';
    clearSelection();
    renderList();
    drawArchitecture();
  };
  $('overview').onclick = () => {
    architectureView = 'overview';
    $('search').value = '';
    $('filter').value = 'all';
    clearSelection();
    renderList();
    drawArchitecture();
  };
  function invalidatePacket() {
    $('agent-prompt').hidden = true;
    $('agent-prompt').value = '';
    $('agent-copy').disabled = true;
    $('agent-status').textContent = '';
    $('packet').hidden = true;
    $('packet').value = '';
    $('copy').disabled = true;
    $('download').disabled = true;
    $('packet-status').textContent = '';
  }
  $('concern').oninput = () => {
    drafts.set(selected, $('concern').value);
    saveReviewState();
    invalidatePacket();
  };
  $('prepare').onclick = () => {
    const node = byId.get(selected);
    const packet = {
      schemaVersion: 1,
      title: `Review ${selected}`,
      concern:
        $('concern').value.trim() || 'Investigate dependencies and confirm intended behavior.',
      module: { path: selected, change: node.change, sha256: node.hash },
      baseline: report.baseline,
      current: report.current,
      dependencies: edges.filter(e => e.source === selected || e.target === selected),
      diagnostics: diagnostics.filter(d => d.file === selected),
      cycles: cycles.filter(c => c.includes(selected)),
      metrics: report.metrics,
      instructions:
        'Verify snapshot freshness before acting. Record an accepted finding in Beads with this evidence. Implement in a separate pass, run relevant checks and regenerate the review.',
    };
    $('packet').value = JSON.stringify(packet, null, 2);
    $('packet').hidden = false;
    $('copy').disabled = false;
    $('download').disabled = false;
    $('packet-status').textContent = 'Packet ready. No issue has been created.';
  };
  $('copy').onclick = async () => {
    try {
      await navigator.clipboard.writeText($('packet').value);
      $('packet-status').textContent = 'Copied.';
    } catch {
      $('packet').focus();
      $('packet').select();
      $('packet-status').textContent = 'Select and copy the packet below.';
    }
  };
  $('download').onclick = () => {
    const url = URL.createObjectURL(new Blob([$('packet').value], { type: 'application/json' }));
    const link = el('a');
    link.href = url;
    link.download = 'review-finding.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  function saveReviewState() {
    try {
      sessionStorage.setItem(
        draftKey,
        JSON.stringify({
          drafts: [...drafts],
          selected,
          search: $('search').value,
          filter: $('filter').value,
        })
      );
    } catch {
      /* navigation still works */
    }
  }
  if (report.live) {
    const pinnedUrl = new URL(location.href);
    pinnedUrl.searchParams.set('baseline', report.baseline.revision);
    history.replaceState(null, '', pinnedUrl);
    $('live-controls').hidden = false;
    for (const baseline of report.live.baselines) {
      const option = el('option', `${short(baseline.revision)} · ${baseline.subject}`);
      option.value = baseline.revision;
      $('baseline').append(option);
    }
    $('baseline').value = report.baseline.revision;
    $('live-controls').onsubmit = () => {
      saveReviewState();
      $('refresh').disabled = true;
      $('refresh-status').textContent = 'Reading current files…';
    };
    addEventListener('pageshow', () => {
      $('refresh').disabled = false;
      $('refresh-status').textContent = 'Baseline stays fixed while you edit.';
    });
    if (saved.search) $('search').value = saved.search;
    if (saved.filter) $('filter').value = saved.filter;
  }
  if (report.live?.agentToken) {
    $('agent-live').hidden = false;
    $('agent-intro').textContent =
      'Launch a separate local Codex agent to review this change. Select a module to focus the review, or use Package overview for the whole diff. Findings appear here; the agent runs in a read-only sandbox. Uses your local Codex sign-in.';
    let run = null,
      polling = null,
      pending = false;
    const running = () => run && ['running', 'stopping'].includes(run.status);
    const drawRun = () => {
      $('agent-launch').disabled = pending || running();
      $('agent-stop').disabled = pending || !running() || run.status === 'stopping';
      if (!run) {
        $('agent-run-status').textContent = 'Ready to review.';
        return;
      }
      $('agent-run-status').textContent = `${run.status}: ${run.progress}`;
      $('agent-run-context').textContent =
        `Baseline ${short(run.baseline)} · ${run.module || 'whole diff'} · started ${new Date(run.startedAt).toLocaleString()} · snapshot ${short(run.fingerprint)}${run.fingerprint !== report.current.fingerprint || run.baseline !== report.baseline.revision ? ' · This run belongs to a different scan than the page.' : ''}`;
      $('agent-findings').textContent = run.output;
      $('agent-findings').hidden = !run.output;
    };
    const request = async body => {
      const response = await fetch('/api/review-run', {
        method: body ? 'POST' : 'GET',
        headers: {
          'X-Review-Token': report.live.agentToken,
          ...(body ? { 'Content-Type': 'application/json' } : {}),
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Unable to connect to the review service.');
      run = data.run;
      drawRun();
    };
    const poll = async () => {
      clearTimeout(polling);
      try {
        await request();
      } catch (error) {
        $('agent-run-status').textContent =
          `Connection interrupted: ${error.message} Reconnecting…`;
      }
      polling = setTimeout(poll, 2000);
    };
    const act = async body => {
      $('agent-run-error').textContent = '';
      pending = true;
      drawRun();
      try {
        await request(body);
      } catch (error) {
        $('agent-run-error').textContent = error.message;
      } finally {
        pending = false;
        $('agent-launch').disabled = running();
        $('agent-stop').disabled = !running() || run.status === 'stopping';
      }
    };
    $('agent-launch').onclick = () => {
      if ($('baseline').value !== report.baseline.revision) {
        $('agent-run-error').textContent =
          'Click Refresh changes to apply the selected baseline before launching.';
        return;
      }
      act({
        action: 'start',
        id: Array.from(crypto.getRandomValues(new Uint8Array(16)), b =>
          b.toString(16).padStart(2, '0')
        ).join(''),
        baseline: report.baseline.revision,
        fingerprint: report.current.fingerprint,
        module: selected,
        concern: selected ? drafts.get(selected) || '' : '',
      });
    };
    $('agent-stop').onclick = () => act({ action: 'stop', id: run.id });
    poll();
  }
  $('agent-prepare').onclick = () => {
    const changed = nodes.filter(n => n.change !== 'unchanged').map(n => `${n.change}: ${n.id}`);
    const context = selected
      ? `Focus on ${selected}. Concern: ${drafts.get(selected) || 'Review its changes and affected callers.'}`
      : 'Review all changes in scope.';
    $('agent-prompt').value = [
      `Review the working-tree changes in ${report.live?.root ?? 'this repository'} against commit ${report.baseline.revision}.`,
      context,
      'Inspect the actual Git diff, changed code, tests and affected callers. Report actionable findings with severity and file/line evidence. Do not edit code unless I ask.',
      `The diagram covers TS/JS implementation files under src/ only. Review other changed files directly as needed.`,
      `Snapshot: ${report.current.fingerprint}. Verify freshness before relying on this evidence.`,
      `Observed changed modules:\n${changed.join('\n') || 'None in scan scope.'}`,
      `Review page: ${location.protocol === 'file:' ? 'local exported snapshot' : location.origin + '/?baseline=' + report.baseline.revision}`,
      'After any authorized changes, refresh the review page against this same baseline.',
    ].join('\n\n');
    $('agent-prompt').hidden = false;
    $('agent-copy').disabled = false;
    $('agent-status').textContent =
      'Request ready to paste into your agent conversation. Preparing this request does not launch an agent.';
  };
  $('agent-copy').onclick = async () => {
    try {
      await navigator.clipboard.writeText($('agent-prompt').value);
      $('agent-status').textContent = 'Copied. Paste into your agent conversation.';
    } catch {
      $('agent-prompt').focus();
      $('agent-prompt').select();
      $('agent-status').textContent =
        'Request selected. Press Ctrl+C or Command+C, then paste into your agent conversation.';
    }
  };
  renderList();
  drawArchitecture();
  if (report.live && saved.selected && visibleNodes().some(n => n.id === saved.selected))
    select(saved.selected);
})();
