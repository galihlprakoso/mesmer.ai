<script>
  import { onMount } from 'svelte'
  import { graphData, selectedNode, activeModuleSet, activeModuleTop, isRunning } from '../lib/stores.js'
  import { buildTree } from '../lib/graph-tree.js'
  import * as d3 from 'd3'

  const ACTIVE_COLOR = '#06b6d4'  // cyan — distinct from frontier blue, promising green, amber human

  let svgEl
  let containerEl
  let tooltipEl
  let width = 800
  let height = 600

  // Live reference to the current active-module set used inside render().
  // Updated via reactive subscription below so we don't need to pass it in
  // every render() call path.
  let activeSetRef = new Set()

  const STATUS_COLORS = {
    dead: '#ef4444',
    promising: '#22c55e',
    frontier: '#3b82f6',
    alive: '#6b7280',
    root: '#a855f7',
  }

  const SOURCE_COLORS = {
    human: '#f59e0b',
  }

  function getGroupColor(d) {
    if (d.data.source === 'human') return SOURCE_COLORS.human
    if (d.data.isGroup) return STATUS_COLORS[d.data.bestStatus] || STATUS_COLORS.alive
    return STATUS_COLORS[d.data.status] || STATUS_COLORS.alive
  }

  function getNodeRadius(d) {
    if (d.data.id === 'root') return 14
    const score = d.data.isGroup ? d.data.bestScore : (d.data.score || 0)
    return Math.max(7, 5 + score * 1.3)
  }

  function truncate(str, len) {
    if (!str) return ''
    return str.length > len ? str.slice(0, len) + '...' : str
  }

  function showTooltip(event, d) {
    if (!tooltipEl) return
    const data = d.data

    let html = ''

    if (data.isGroup) {
      // Group tooltip
      const statusLabel = data.bestStatus.toUpperCase()
      html += `<div class="tt-header">`
      html += `<span class="tt-status tt-${data.bestStatus}">${statusLabel}</span>`
      html += `<span class="tt-score">${data.bestScore}/10</span>`
      html += `</div>`
      html += `<div class="tt-module">${data.module}</div>`
      html += `<div class="tt-approach">${data.attemptCount} attempts &mdash; scores: ${data.scores.join(', ')}</div>`
      if (data.leaked_info) html += `<div class="tt-leaked">${truncate(data.leaked_info, 100)}</div>`

      // Show each attempt briefly
      html += `<div class="tt-attempts">`
      for (const a of data.attempts) {
        const color = STATUS_COLORS[a.status] || STATUS_COLORS.alive
        const score = a.score ?? 0
        const approach = a.approach ? truncate(a.approach, 60) : '(no approach)'
        html += `<div class="tt-attempt"><span class="tt-attempt-dot" style="background:${color}"></span>${score}/10 &mdash; ${approach}</div>`
      }
      html += `</div>`
    } else {
      // Single node tooltip
      const statusLabel = data.source === 'human' ? 'HUMAN HINT' : (data.status || 'unknown').toUpperCase()
      const statusClass = data.source === 'human' ? 'human' : data.status
      html += `<div class="tt-header">`
      html += `<span class="tt-status tt-${statusClass}">${statusLabel}</span>`
      if (data.score) html += `<span class="tt-score">${data.score}/10</span>`
      html += `</div>`
      html += `<div class="tt-module">${data.module || data.id}</div>`
      if (data.approach) html += `<div class="tt-approach">${truncate(data.approach, 100)}</div>`
      if (data.leaked_info) html += `<div class="tt-leaked">${truncate(data.leaked_info, 100)}</div>`
    }

    tooltipEl.innerHTML = html
    tooltipEl.style.display = 'block'

    const rect = containerEl.getBoundingClientRect()
    let x = event.clientX - rect.left + 14
    let y = event.clientY - rect.top - 10
    const ttRect = tooltipEl.getBoundingClientRect()
    if (x + ttRect.width > rect.width - 8) x = x - ttRect.width - 28
    if (y + ttRect.height > rect.height - 8) y = rect.height - ttRect.height - 8
    if (y < 8) y = 8

    tooltipEl.style.left = x + 'px'
    tooltipEl.style.top = y + 'px'
  }

  function hideTooltip() {
    if (tooltipEl) tooltipEl.style.display = 'none'
  }

  /** Get all ancestor node references for path highlighting */
  function getAncestors(node) {
    const path = []
    let current = node
    while (current) {
      path.push(current)
      current = current.parent
    }
    return path
  }

  function render(data) {
    if (!svgEl || !data) return

    const svg = d3.select(svgEl)
    svg.selectAll('*').remove()

    const tree = buildTree(data)
    if (!tree) {
      svg.append('text')
        .attr('x', width / 2)
        .attr('y', height / 2)
        .attr('text-anchor', 'middle')
        .attr('fill', 'var(--text-muted)')
        .text('No graph data yet')
      return
    }

    const root = d3.hierarchy(tree)
    const nodeCount = root.descendants().length
    const radius = Math.min(width, height) / 2 - 60

    const treeLayout = d3.tree()
      .size([2 * Math.PI, radius])
      .separation((a, b) => (a.parent === b.parent ? 1 : 2) / (a.depth || 1))

    treeLayout(root)

    const g = svg.append('g')
      .attr('transform', `translate(${width / 2},${height / 2})`)

    // Zoom
    const zoom = d3.zoom()
      .scaleExtent([0.3, 4])
      .on('zoom', (event) => {
        g.attr('transform', `translate(${width / 2 + event.transform.x},${height / 2 + event.transform.y}) scale(${event.transform.k})`)
      })
    svg.call(zoom)

    // Click background to deselect
    svg.on('click', () => {
      $selectedNode = null
      hideTooltip()
      resetHighlight()
    })

    // Links
    const links = g.selectAll('.link')
      .data(root.links())
      .join('path')
      .attr('class', 'link')
      .attr('fill', 'none')
      .attr('stroke', d => getGroupColor(d.target))
      .attr('stroke-opacity', 0.35)
      .attr('stroke-width', d => {
        const score = d.target.data.isGroup ? d.target.data.bestScore : (d.target.data.score || 0)
        return Math.max(1.5, score / 2.5)
      })
      .attr('d', d3.linkRadial()
        .angle(d => d.x)
        .radius(d => d.y)
      )

    // Nodes
    const allNodes = root.descendants()
    const nodeGroup = g.selectAll('.node')
      .data(allNodes)
      .join('g')
      .attr('class', 'node')
      .attr('transform', d => `rotate(${d.x * 180 / Math.PI - 90}) translate(${d.y},0)`)
      .style('cursor', 'pointer')
      .on('click', (event, d) => {
        event.stopPropagation()
        $selectedNode = d.data
        hideTooltip()
      })
      .on('mouseenter', (event, d) => {
        showTooltip(event, d)
        highlightPath(d, allNodes, links)
        d3.select(event.currentTarget).select('.main-circle')
          .transition().duration(150)
          .attr('r', getNodeRadius(d) + 3)
      })
      .on('mousemove', (event, d) => {
        showTooltip(event, d)
      })
      .on('mouseleave', (event, d) => {
        hideTooltip()
        resetHighlight()
        d3.select(event.currentTarget).select('.main-circle')
          .transition().duration(150)
          .attr('r', getNodeRadius(d))
      })

    // --- Render single nodes ---
    const singleNodes = nodeGroup.filter(d => !d.data.isGroup)

    singleNodes.append('circle')
      .attr('class', 'main-circle')
      .attr('r', d => getNodeRadius(d))
      .attr('fill', d => getGroupColor(d))
      .attr('fill-opacity', 0.85)

    // --- Render group nodes with ring segments ---
    const groupNodes = nodeGroup.filter(d => d.data.isGroup)

    // Base circle (best status color)
    groupNodes.append('circle')
      .attr('class', 'main-circle')
      .attr('r', d => getNodeRadius(d))
      .attr('fill', d => getGroupColor(d))
      .attr('fill-opacity', 0.85)

    // Ring segments showing each attempt
    groupNodes.each(function(d) {
      const g = d3.select(this)
      const r = getNodeRadius(d) + 4
      const attempts = d.data.attempts
      const n = attempts.length
      const arcGen = d3.arc()
        .innerRadius(r)
        .outerRadius(r + 3)

      attempts.forEach((attempt, i) => {
        const startAngle = (i / n) * 2 * Math.PI
        const endAngle = ((i + 1) / n) * 2 * Math.PI - 0.08 // small gap
        const color = STATUS_COLORS[attempt.status] || STATUS_COLORS.alive

        g.append('path')
          .attr('d', arcGen({ startAngle, endAngle }))
          .attr('fill', color)
          .attr('fill-opacity', 0.9)
      })
    })

    // Attempt count badge on groups
    groupNodes.filter(d => d.data.attemptCount > 1)
      .append('text')
      .attr('dy', '0.35em')
      .attr('text-anchor', 'middle')
      .attr('fill', '#000')
      .attr('font-size', '8px')
      .attr('font-weight', '700')
      .attr('pointer-events', 'none')
      .text(d => `${d.data.bestScore}`)

    // Score labels on high-score single nodes
    singleNodes.filter(d => d.data.score >= 5 && d.data.id !== 'root')
      .append('text')
      .attr('dy', '0.35em')
      .attr('text-anchor', 'middle')
      .attr('fill', '#000')
      .attr('font-size', '9px')
      .attr('font-weight', '700')
      .attr('pointer-events', 'none')
      .text(d => d.data.score)

    // --- Active-module pulse (run in progress) ---
    // Highlight every node whose module (or any grouped attempt's module) is in
    // the active stack. Pulsing cyan outer ring + inner glow.
    const activeSet = activeSetRef
    const isNodeActive = (d) => {
      if (!activeSet || activeSet.size === 0) return false
      if (d.data.id === 'root') return false
      if (d.data.status === 'dead') return false
      if (activeSet.has(d.data.module)) return true
      // Group nodes: check their attempts
      if (d.data.isGroup && Array.isArray(d.data.attempts)) {
        return d.data.attempts.some(a => activeSet.has(a.module))
      }
      return false
    }

    const activeNodes = nodeGroup.filter(d => isNodeActive(d))

    // Outer pulsing ring
    const pulseRing = activeNodes.append('circle')
      .attr('class', 'active-pulse')
      .attr('fill', 'none')
      .attr('stroke', ACTIVE_COLOR)
      .attr('stroke-width', 2)
      .attr('r', d => getNodeRadius(d) + 6)
      .style('pointer-events', 'none')

    pulseRing.append('animate')
      .attr('attributeName', 'r')
      .attr('values', d => {
        const r = getNodeRadius(d)
        return `${r + 4};${r + 14};${r + 4}`
      })
      .attr('dur', '1.3s')
      .attr('repeatCount', 'indefinite')

    pulseRing.append('animate')
      .attr('attributeName', 'stroke-opacity')
      .attr('values', '0.9;0.1;0.9')
      .attr('dur', '1.3s')
      .attr('repeatCount', 'indefinite')

    // Inner glow via a slightly brighter overlay
    activeNodes.append('circle')
      .attr('class', 'active-glow')
      .attr('fill', ACTIVE_COLOR)
      .attr('fill-opacity', 0.15)
      .attr('r', d => getNodeRadius(d) + 1)
      .style('pointer-events', 'none')

    // --- Path highlighting ---
    function highlightPath(hoveredNode, allNodes, links) {
      const ancestors = new Set(getAncestors(hoveredNode))

      // Dim everything
      nodeGroup.select('.main-circle')
        .transition().duration(200)
        .attr('fill-opacity', d => ancestors.has(d) ? 0.95 : 0.15)

      links
        .transition().duration(200)
        .attr('stroke-opacity', d => (ancestors.has(d.source) && ancestors.has(d.target)) ? 0.7 : 0.07)
    }

    function resetHighlight() {
      nodeGroup.select('.main-circle')
        .transition().duration(200)
        .attr('fill-opacity', 0.85)

      links
        .transition().duration(200)
        .attr('stroke-opacity', 0.35)
    }
  }

  onMount(() => {
    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        width = entry.contentRect.width
        height = entry.contentRect.height
        if ($graphData) render($graphData)
      }
    })
    resizeObserver.observe(containerEl)
    return () => resizeObserver.disconnect()
  })

  // Re-render whenever the graph OR the active-module set changes.
  // Keep activeSetRef in sync FIRST so render() reads the latest value.
  $: activeSetRef = $activeModuleSet
  $: {
    // eslint-disable-next-line no-unused-expressions
    $activeModuleSet  // track dep for reactivity on active pulse
    if (svgEl) {
      if ($graphData) {
        render($graphData)
      } else {
        // Graph reset (new run starting) — clear any leftover D3 render so
        // the empty-state overlay isn't stacked on stale nodes.
        d3.select(svgEl).selectAll('*').remove()
      }
    }
  }
</script>

<div class="graph-container" bind:this={containerEl}>
  {#if !$graphData}
    <div class="empty-graph">
      <div class="empty-icon">&#x1f578;</div>
      <p>Attack graph will appear here</p>
      <p class="sub">Run an attack or load a saved graph</p>
    </div>
  {/if}
  <svg bind:this={svgEl} {width} {height}></svg>

  {#if $isRunning}
    <div class="running-ribbon">
      <span class="ribbon-pulse"></span>
      <span class="ribbon-text">
        RUNNING
        {#if $activeModuleTop}
          <span class="ribbon-sep">·</span>
          <span class="ribbon-module">{$activeModuleTop}</span>
        {/if}
      </span>
    </div>
  {/if}

  <div class="tooltip" bind:this={tooltipEl}></div>

  <div class="legend">
    <span class="legend-item"><span class="dot" style="background: {STATUS_COLORS.promising}"></span> Promising</span>
    <span class="legend-item"><span class="dot" style="background: {STATUS_COLORS.dead}"></span> Dead</span>
    <span class="legend-item"><span class="dot" style="background: {STATUS_COLORS.alive}"></span> Alive</span>
    <span class="legend-item"><span class="dot-ring"></span> Group (multi-attempt)</span>
    {#if $isRunning}
      <span class="legend-item"><span class="dot-active"></span> Active</span>
    {/if}
  </div>
</div>

<style>
  .graph-container {
    position: relative;
    flex: 1;
    overflow: hidden;
    background: var(--bg-primary);
  }

  svg { width: 100%; height: 100%; }

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

  /* Tooltip */
  .tooltip {
    display: none;
    position: absolute;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 14px;
    max-width: 360px;
    pointer-events: none;
    z-index: 100;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  }

  .tooltip :global(.tt-header) { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .tooltip :global(.tt-status) { padding: 1px 6px; border-radius: 3px; font-size: 0.6rem; font-weight: 700; letter-spacing: 0.06em; background: var(--bg-secondary); color: var(--text-muted); }
  .tooltip :global(.tt-dead) { color: var(--red); background: #ef44441a; }
  .tooltip :global(.tt-promising) { color: var(--green); background: #22c55e1a; }
  .tooltip :global(.tt-alive) { color: var(--text-muted); }
  .tooltip :global(.tt-human) { color: var(--amber); background: #f59e0b1a; }
  .tooltip :global(.tt-score) { font-weight: 700; font-size: 0.85rem; color: var(--green); }
  .tooltip :global(.tt-module) { font-weight: 600; font-size: 0.82rem; color: var(--text); margin-bottom: 4px; }
  .tooltip :global(.tt-approach) { font-size: 0.75rem; color: var(--text-muted); line-height: 1.4; margin-bottom: 4px; }
  .tooltip :global(.tt-leaked) { font-size: 0.72rem; color: var(--green); border-left: 2px solid var(--green); padding-left: 6px; margin-bottom: 4px; }
  .tooltip :global(.tt-meta) { font-size: 0.65rem; color: var(--text-muted); }
  .tooltip :global(.tt-attempts) { margin-top: 6px; border-top: 1px solid var(--border); padding-top: 6px; }
  .tooltip :global(.tt-attempt) { font-size: 0.7rem; color: var(--text-muted); display: flex; align-items: baseline; gap: 5px; margin-bottom: 2px; }
  .tooltip :global(.tt-attempt-dot) { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; margin-top: 2px; }

  /* Legend */
  .legend {
    position: absolute;
    bottom: 12px;
    right: 12px;
    display: flex;
    gap: 12px;
    font-size: 0.7rem;
    color: var(--text-muted);
    background: var(--bg-secondary);
    padding: 6px 10px;
    border-radius: 6px;
    border: 1px solid var(--border);
  }

  .legend-item { display: flex; align-items: center; gap: 4px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .dot-ring {
    width: 10px; height: 10px; border-radius: 50%;
    border: 2px solid var(--text-muted);
    display: inline-block;
  }
  .dot-active {
    width: 10px; height: 10px; border-radius: 50%;
    border: 2px solid #06b6d4;
    background: #06b6d422;
    display: inline-block;
  }

  /* Running ribbon */
  .running-ribbon {
    position: absolute;
    top: 12px;
    right: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 12px;
    background: #06b6d41a;
    border: 1px solid #06b6d4;
    border-radius: 20px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    color: #06b6d4;
    z-index: 50;
    animation: ribbonFadeIn 0.2s ease-out;
  }

  .ribbon-pulse {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #06b6d4;
    box-shadow: 0 0 0 0 #06b6d4;
    animation: ribbonPulse 1.3s infinite;
  }

  .ribbon-text { display: flex; align-items: center; gap: 6px; }
  .ribbon-sep { opacity: 0.5; }
  .ribbon-module {
    font-family: monospace;
    font-weight: 600;
    text-transform: none;
    letter-spacing: 0;
  }

  @keyframes ribbonFadeIn {
    from { opacity: 0; transform: translateY(-4px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  @keyframes ribbonPulse {
    0%   { box-shadow: 0 0 0 0 rgba(6, 182, 212, 0.7); }
    70%  { box-shadow: 0 0 0 8px rgba(6, 182, 212, 0); }
    100% { box-shadow: 0 0 0 0 rgba(6, 182, 212, 0); }
  }
</style>
