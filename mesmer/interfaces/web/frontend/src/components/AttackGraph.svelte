<script>
  import { onMount } from 'svelte'
  import {
    graphData,
    selectedNode,
    activeModuleSet,
    activeModuleTop,
    scenarios,
    selectedScenario,
    selectedRunId,
  } from '../lib/stores.js'
  import { buildLeaderTimeline } from '../lib/leader-timeline.js'
  import RunPicker from './RunPicker.svelte'
  import * as d3 from 'd3'

  // Status drives node color — tier is a scheduling concern, not a thing
  // a human watching the run cares about. Frontier and the leader-verdict
  // square keep their own visual treatments below.
  const STATUS_COLOR = {
    promising: 'var(--phosphor)',
    dead:      'var(--red)',
    alive:     'var(--text-muted)',
  }

  let svgEl
  let containerEl
  let width = 800
  let height = 600
  let collapsed = new Set()
  let zoomBehavior
  let initialTransformDone = false

  // Live refs so the reactive block stays a single subscription chain.
  let activeSetRef = new Set()
  let activeTopRef = null
  let selectedIdRef = null
  let scenarioMetaRef = null

  function isLeaderVerdict(node) {
    return node.source === 'leader'
  }

  function isFrontier(node) {
    return node.status === 'frontier'
  }

  function colorFor(node) {
    if (node._isLeaderOrchestrator) return 'var(--phosphor)'
    if (isLeaderVerdict(node)) {
      return node.status === 'promising' ? 'var(--phosphor)' : 'var(--red)'
    }
    return STATUS_COLOR[node.status] ?? 'var(--text-muted)'
  }

  function shortLabel(node) {
    // The shape + color already tell the story (square = leader verdict,
    // green/red = win/loss, dashed = proposed, pulse = active). The label
    // is just the module name — no scores, no verdict suffix, no clutter.
    if (node._isLeaderOrchestrator) return node.module || 'leader'
    if (isFrontier(node)) return `${node.module} · proposed`
    return node.module
  }

  function isNodeActive(data) {
    if (!activeSetRef || activeSetRef.size === 0) return false
    if (data._isLeaderOrchestrator) return false
    if (data.status === 'dead') return false
    return activeSetRef.has(data.module)
  }

  function nodeClass(d) {
    const data = d.data
    const parts = ['node']
    if (data._isLeaderOrchestrator) parts.push('leader-orchestrator')
    else if (isLeaderVerdict(data)) parts.push('leader-verdict')
    else if (isFrontier(data)) parts.push('frontier')
    else if (data.status === 'dead') parts.push('dead')
    if (selectedIdRef && data.id === selectedIdRef) parts.push('selected')
    if (isNodeActive(data)) parts.push('active')
    return parts.join(' ')
  }

  /* ---------- toolbar handlers ---------- */

  function fit() {
    if (!svgEl || !zoomBehavior) return
    const layer = d3.select(svgEl).select('.node-layer').node()
    if (!layer) return
    const bbox = layer.getBBox()
    const W = svgEl.clientWidth
    const H = svgEl.clientHeight
    if (!W || !H || !bbox.width || !bbox.height) return
    const pad = 30
    const scale = Math.min(
      (W - pad * 2) / bbox.width,
      (H - pad * 2) / bbox.height,
      1.2,
    )
    const tx = (W - bbox.width * scale) / 2 - bbox.x * scale
    const ty = (H - bbox.height * scale) / 2 - bbox.y * scale
    d3.select(svgEl)
      .transition()
      .duration(300)
      .call(zoomBehavior.transform, d3.zoomIdentity.translate(tx, ty).scale(scale))
  }

  function expandAll() {
    collapsed = new Set()
    render()
  }

  function collapseAll() {
    if (!$graphData?.nodes) return
    const next = new Set()
    for (const n of Object.values($graphData.nodes)) {
      const hasChildren = (n.children || []).length > 0
      if (hasChildren && n.status !== 'frontier' && n.source !== 'leader') {
        next.add(n.id)
      }
    }
    collapsed = next
    render()
  }

  /* ---------- render ---------- */

  function render() {
    if (!svgEl) return

    const svg = d3.select(svgEl)
    svg.selectAll('*').remove()

    if (!$graphData) return

    const tree = buildLeaderTimeline($graphData, collapsed, scenarioMetaRef, $selectedRunId)
    if (!tree) return

    const root = d3.hierarchy(tree)
    const nodeHSpace = 200
    const nodeVSpace = 56
    d3.tree().nodeSize([nodeVSpace, nodeHSpace])(root)

    const zoomLayer = svg.append('g').attr('class', 'zoom-layer')

    // Recreate zoom behavior every render so its `zoom` handler captures the
    // FRESH zoomLayer reference. The old layer is gone (svg.selectAll('*')
    // removed it), so a stale closure would silently apply transforms to a
    // detached DOM node — pan/zoom appear dead until a full reload.
    if (!zoomBehavior) zoomBehavior = d3.zoom().scaleExtent([0.25, 4])
    zoomBehavior.on('zoom', e => zoomLayer.attr('transform', e.transform))
    svg.call(zoomBehavior)

    // Restore prior pan/zoom on re-render (the user's transform survives
    // graph updates). Only centre on the very first render.
    const currentTransform = d3.zoomTransform(svg.node())
    if (!initialTransformDone) {
      const initialTx = 80
      const initialTy = height / 2
      svg.call(zoomBehavior.transform, d3.zoomIdentity.translate(initialTx, initialTy).scale(1))
      initialTransformDone = true
    } else {
      // Re-apply the existing transform to the new zoomLayer.
      zoomLayer.attr('transform', currentTransform.toString())
    }

    // Background click clears selection. Only fire when the click target
    // really is the SVG itself — not bubbled up from a node — so selecting
    // a node doesn't immediately clear it again.
    svg.on('click', (event) => {
      if (event.target === svgEl) selectedNode.set(null)
    })

    // ---------- edges ----------
    zoomLayer.append('g')
      .attr('class', 'link-layer')
      .selectAll('path')
      .data(root.links())
      .join('path')
      .attr('class', 'link')
      .attr('d', d3.linkHorizontal().x(d => d.y).y(d => d.x))

    // ---------- nodes ----------
    const nodeLayer = zoomLayer.append('g').attr('class', 'node-layer')

    const nodeSel = nodeLayer.selectAll('g')
      .data(root.descendants(), d => d.data.id)
      .join('g')
      .attr('class', d => nodeClass(d))
      .attr('transform', d => `translate(${d.y},${d.x})`)
      .on('click', (event, d) => {
        event.stopPropagation()
        selectedNode.set(d.data)
      })

    // Diamond — synthetic leader orchestrator
    nodeSel.filter(d => d.data._isLeaderOrchestrator)
      .append('polygon')
      .attr('points', '0,-14 14,0 0,14 -14,0')
      .attr('fill', d => colorFor(d.data))

    // Square — leader verdict
    nodeSel.filter(d => isLeaderVerdict(d.data))
      .append('rect')
      .attr('x', -14).attr('y', -14)
      .attr('width', 28).attr('height', 28).attr('rx', 3)
      .attr('fill', d => colorFor(d.data))

    // Circle — everything else
    nodeSel.filter(d => !d.data._isLeaderOrchestrator && !isLeaderVerdict(d.data))
      .append('circle')
      .attr('r', d => isFrontier(d.data) ? 11 : 14)
      .attr('fill', d => colorFor(d.data))

    // Primary label below
    nodeSel.append('text')
      .attr('y', 30)
      .attr('text-anchor', 'middle')
      .text(d => shortLabel(d.data))

    // Sequence number above leader's direct children
    nodeSel.filter(d => typeof d.data._seqNum === 'number')
      .append('text')
      .attr('class', 'seq-num')
      .attr('y', -20)
      .attr('text-anchor', 'middle')
      .text(d => `#${d.data._seqNum}`)

    // Human-source badge (hint nodes)
    nodeSel.filter(d => d.data.source === 'human')
      .append('text')
      .attr('class', 'human-badge')
      .attr('x', 0).attr('y', -22)
      .attr('text-anchor', 'middle')
      .text('★')

    // Collapsed indicator — "+" if we're hiding children. Skip the leader
    // root (whether real verdict or synthetic stub): its _childIds vs
    // children mismatch is intentional since it gets a curated child list
    // (attempts only) regardless of what's stored.
    nodeSel.filter(d =>
      !d.data._isLeaderRoot
      && (d.data._childIds || []).length > (d.data.children || []).length
    )
      .append('text')
      .attr('class', 'collapsed-mark')
      .attr('x', 18).attr('y', 6)
      .attr('text-anchor', 'start')
      .text('+')
  }

  /* ---------- lifecycle ---------- */

  onMount(() => {
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        width = entry.contentRect.width
        height = entry.contentRect.height
      }
      render()
    })
    ro.observe(containerEl)
    return () => ro.disconnect()
  })

  // Refs read inside render() — assignments must happen before the render
  // block fires (Svelte processes reactive declarations in source order).
  $: activeSetRef = $activeModuleSet
  $: activeTopRef = $activeModuleTop
  $: selectedIdRef = $selectedNode?.id ?? null
  $: {
    const s = ($scenarios || []).find(s => s.path === $selectedScenario)
    // ``s.modules`` (plural list) is the current shape; ``s.module`` is
    // the legacy field still emitted by older list endpoints. Pick the
    // first manager as the canonical "leader module" label for the
    // scenario header — multi-manager scenarios show the first.
    const firstModule = (s && Array.isArray(s.modules) && s.modules[0])
      || (s && s.module)
      || ''
    scenarioMetaRef = s ? { leaderModule: firstModule, objective: '' } : null
  }

  // Full re-render when graph structure / scenario / active set / run
  // selection changes. NOT triggered by selection changes — that would
  // tear down the SVG and break d3.zoom (the handler closes over a
  // deleted layer).
  $: {
    void $graphData
    void $activeModuleSet
    void scenarioMetaRef
    void $selectedRunId
    if (svgEl) render()
  }

  // Selection updates just toggle the .selected class on the existing
  // nodes — no re-render, so pan/zoom state and behavior stay intact.
  $: if (svgEl) {
    d3.select(svgEl).selectAll('g.node')
      .classed('selected', d => d?.data?.id === selectedIdRef)
  }
</script>

<div class="graph-container" bind:this={containerEl}>
  <div class="graph-chrome">
    <span class="chrome-dot live" aria-hidden="true"></span>
    <span class="chrome-dot" aria-hidden="true"></span>
    <span class="chrome-dot" aria-hidden="true"></span>
    <span class="chrome-label">attack graph ▸ live</span>
  </div>

  {#if !$graphData}
    <div class="empty-graph">
      <div class="empty-icon">&#x1f578;</div>
      <p>Attack graph will appear here</p>
      <p class="sub">Run an attack or load a saved graph</p>
    </div>
  {/if}

  <svg bind:this={svgEl} {width} {height}></svg>

  <!-- Run picker (top-right). Subsumes the old running-ribbon: the
       currently-running run's chip pulses in cyan and shows the active
       module name as a sibling tag. -->
  <div class="run-picker-host">
    <RunPicker />
  </div>

  <div class="toolbar">
    <button on:click={fit}>Fit</button>
    <button on:click={expandAll}>Expand all</button>
    <button on:click={collapseAll}>Collapse</button>
  </div>

  <div class="legend">
    <span class="li"><span class="status-dot worked"></span>Worked</span>
    <span class="li"><span class="status-dot dead"></span>Dead end</span>
    <span class="li"><span class="frontier-dot"></span>Up next</span>
  </div>
</div>

<style>
  .graph-container {
    position: relative;
    flex: 1;
    overflow: hidden;
    background: var(--bg-primary);
  }

  svg { width: 100%; height: 100%; display: block; }

  .graph-chrome {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    z-index: 4;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 14px;
    background: linear-gradient(to bottom, hsla(144 12% 10% / 0.85), hsla(144 12% 10% / 0));
    pointer-events: none;
  }

  .chrome-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: hsla(140 7% 45% / 0.5);
  }

  .chrome-dot.live {
    background: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
    animation: phosphor-pulse 2s ease-in-out infinite;
  }

  @keyframes phosphor-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.55; }
  }

  .chrome-label {
    margin-left: 4px;
    font-family: var(--font-pixel);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.6875rem;
    color: var(--text-muted);
  }

  .empty-graph {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
    pointer-events: none;
  }
  .empty-icon { font-size: 3rem; margin-bottom: 12px; opacity: 0.3; }
  .empty-graph p { margin: 0; font-size: 0.9rem; }
  .empty-graph .sub { font-size: 0.75rem; margin-top: 4px; opacity: 0.6; }

  /* ---------- nodes ---------- */
  .graph-container :global(.node) { cursor: pointer; }
  .graph-container :global(.node circle),
  .graph-container :global(.node rect),
  .graph-container :global(.node polygon) {
    stroke: rgba(255, 255, 255, 0.25);
    stroke-width: 1.5px;
    transition: r 120ms;
  }
  .graph-container :global(.node:hover circle) { r: 16; }

  .graph-container :global(.node.frontier circle) {
    fill-opacity: 0.35;
    stroke-dasharray: 4 3;
    stroke-opacity: 0.7;
  }
  .graph-container :global(.node.frontier text) { opacity: 0.65; }

  .graph-container :global(.node.dead circle) {
    stroke-dasharray: 3 2;
    opacity: 0.7;
  }

  .graph-container :global(.node.selected circle),
  .graph-container :global(.node.selected rect),
  .graph-container :global(.node.selected polygon) {
    stroke: var(--accent);
    stroke-width: 3.5px;
    filter: drop-shadow(0 0 8px var(--accent));
  }

  .graph-container :global(.node .human-badge) {
    fill: var(--amber);
    font-size: 12px;
    font-weight: 700;
    pointer-events: none;
  }
  .graph-container :global(.node .collapsed-mark) {
    fill: var(--text-muted);
    font-size: 14px;
    font-weight: 700;
    pointer-events: none;
  }

  .graph-container :global(.node text) {
    fill: var(--text);
    font-size: 11px;
    font-family: var(--mono);
    pointer-events: none;
  }
  .graph-container :global(.node .seq-num) {
    fill: var(--text-muted);
    font-size: 9px;
    font-family: var(--mono);
    pointer-events: none;
  }

  /* Synthetic leader (diamond) */
  .graph-container :global(.node.leader-orchestrator polygon) {
    fill: var(--accent);
    fill-opacity: 0.18;
    stroke: var(--accent);
    stroke-width: 2px;
  }
  .graph-container :global(.node.leader-orchestrator text) {
    fill: var(--accent);
    font-weight: 600;
  }

  /* Leader verdict (square) */
  .graph-container :global(.node.leader-verdict rect) {
    filter: drop-shadow(0 0 4px rgba(255, 255, 255, 0.3));
  }

  /* Active-module pulse — live-only signal not in bench. Phosphor stroke +
     glow on whichever node's module the agent is currently inside. */
  .graph-container :global(.node.active circle),
  .graph-container :global(.node.active rect),
  .graph-container :global(.node.active polygon) {
    stroke: var(--phosphor);
    stroke-width: 2.5px;
    animation: nodePulse 1.4s ease-in-out infinite;
  }
  @keyframes nodePulse {
    0%, 100% { filter: drop-shadow(0 0 2px var(--phosphor)); }
    50%      { filter: drop-shadow(0 0 10px var(--phosphor)); }
  }

  /* ---------- links ---------- */
  .graph-container :global(.link) {
    fill: none;
    stroke: var(--border);
    stroke-width: 1.2px;
  }

  /* ---------- toolbar ---------- */
  .toolbar {
    position: absolute;
    top: 38px;
    left: 12px;
    display: flex;
    gap: 6px;
    z-index: 10;
  }
  .toolbar button {
    padding: 4px 10px;
    background: var(--bg-tertiary);
    color: var(--text-muted);
    border: 1px solid var(--border);
    border-radius: 3px;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: border-color 100ms, color 100ms, box-shadow 100ms;
  }
  .toolbar button:hover {
    border-color: var(--phosphor);
    color: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
  }

  /* ---------- legend ---------- */
  .legend {
    position: absolute;
    bottom: 12px;
    right: 12px;
    display: flex;
    gap: 14px;
    align-items: center;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    background: var(--bg-secondary);
    padding: 6px 12px;
    border-radius: 4px;
    border: 1px solid var(--border);
  }
  .legend .li { display: inline-flex; align-items: center; gap: 5px; }
  .status-dot {
    width: 9px; height: 9px; border-radius: 50%;
    display: inline-block;
  }
  .status-dot.worked { background: var(--phosphor); box-shadow: var(--phosphor-glow-tight); }
  .status-dot.dead { background: var(--red); }
  .frontier-dot {
    width: 9px; height: 9px; border-radius: 50%;
    border: 1.5px dashed var(--text-muted);
    background: transparent;
    display: inline-block;
    opacity: 0.7;
  }

  /* ---------- run picker host ---------- */
  .run-picker-host {
    position: absolute;
    top: 38px;
    right: 12px;
    z-index: 10;
  }
</style>
