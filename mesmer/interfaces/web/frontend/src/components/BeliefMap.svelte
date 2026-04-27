<script>
  /**
   * Belief Attack Graph view.
   *
   * Layout mirrors AttackGraph.svelte's visual language so the operator
   * sees both views as one product, not two:
   *   - graph-chrome strip across the top (pulse dot + pixel label)
   *   - toolbar pill (top-left): Refresh + collapse / expand inspector
   *   - run picker (top-right) is owned by App; we leave room for it
   *   - canvas fills the center; legend sits in the bottom-right
   *   - right-side INSPECTOR rail: tabs for Frontier / Evidence /
   *     Strategy lists, with BeliefNodeDetail overlaying the rail when a
   *     node is selected (so the lists keep their state behind it).
   *
   * The graph itself renders the typed planner state as a force-directed
   * layout:
   *   - WeaknessHypothesis nodes are circles, sized by confidence.
   *   - Evidence nodes are triangles colored by polarity.
   *   - FrontierExperiment nodes are squares, sized by utility.
   *   - Strategy nodes are diamonds.
   *   - Attempt nodes are small dots (de-emphasised audit trail).
   *   - Target node is a star.
   *
   * Distinct from `AttackGraph.svelte` which renders `AttackGraph` as a
   * hierarchical tree of execution history. The belief
   * map answers "what does the planner believe right now?"; the attack
   * graph answers "what did we try?".
   */
  import { onDestroy, onMount } from 'svelte'
  import { get } from 'svelte/store'
  import * as d3 from 'd3'
  import { getBeliefGraph } from '../lib/api.js'
  import { runStatus, selectedBeliefNode } from '../lib/stores.js'
  import BeliefNodeDetail from './BeliefNodeDetail.svelte'

  /** @type {string|null} */
  export let targetHash = null

  let containerEl
  let canvasEl
  let svgEl
  let width = 800
  let height = 600

  let loading = false
  let errorMsg = ''
  let graph = null
  let stats = null
  let promptContext = ''
  let simulation = null
  let zoomBehavior = null
  let pollTimer = null

  // ---- inspector rail state (collapse + active list tab) ----
  // Persist the rail open/closed preference; some operators run with
  // the canvas full-bleed and only flip the rail open when they need it.
  let inspectorOpen = (() => {
    try {
      const v = localStorage.getItem('mesmer.beliefmap.inspectorOpen')
      return v === null ? true : v === 'true'
    } catch {
      return true
    }
  })()
  $: try { localStorage.setItem('mesmer.beliefmap.inspectorOpen', String(inspectorOpen)) } catch {}

  /** @type {'frontier' | 'evidence' | 'strategy'} */
  let inspectorTab = 'frontier'

  // ---- legend collapse ----
  let legendOpen = (() => {
    try {
      const v = localStorage.getItem('mesmer.beliefmap.legendOpen')
      return v === null ? true : v === 'true'
    } catch {
      return true
    }
  })()
  $: try { localStorage.setItem('mesmer.beliefmap.legendOpen', String(legendOpen)) } catch {}

  // Color palette aligned with AttackGraph.svelte for cross-view
  // continuity. Status semantics transfer: confirmed/promising = phosphor
  // green, refuted/dead = red, etc.
  const COLOR = {
    hypothesis_active: 'var(--text)',
    hypothesis_confirmed: 'var(--phosphor)',
    hypothesis_refuted: 'var(--red)',
    hypothesis_stale: 'var(--text-muted)',
    evidence_supports: 'var(--phosphor)',
    evidence_refutes: 'var(--red)',
    evidence_neutral: 'var(--text-muted)',
    frontier_proposed: 'var(--amber, #d4a017)',
    frontier_executing: 'var(--amber, #d4a017)',
    frontier_fulfilled: 'var(--text-muted)',
    frontier_dropped: 'var(--text-muted)',
    strategy: '#a78bfa',
    attempt: 'var(--text-muted)',
    target: 'var(--phosphor)',
  }

  $: void targetHash, void load()
  $: graphNodes = graph?.nodes || []
  $: frontierBoard = graphNodes
    .filter((n) => n.kind === 'frontier' && n.state === 'proposed')
    .slice()
    .sort((a, b) => (b.utility ?? 0) - (a.utility ?? 0))
    .slice(0, 12)
  $: evidenceTimeline = graphNodes
    .filter((n) => n.kind === 'evidence')
    .slice()
    .sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0))
    .slice(0, 12)
  $: strategyRows = graphNodes
    .filter((n) => n.kind === 'strategy')
    .slice()
    .sort((a, b) => {
      const ar = a.attempt_count ? a.success_count / a.attempt_count : 0
      const br = b.attempt_count ? b.success_count / b.attempt_count : 0
      return br - ar
    })
    .slice(0, 12)

  async function load() {
    if (!targetHash) {
      graph = null
      stats = null
      promptContext = ''
      errorMsg = ''
      return
    }
    loading = true
    errorMsg = ''
    try {
      const data = await getBeliefGraph(targetHash)
      graph = data.graph
      stats = data.stats
      promptContext = data.prompt_context || ''
      // Defer render to allow Svelte to commit svgEl before we measure
      // container dimensions.
      requestAnimationFrame(render)
    } catch (e) {
      const initializing = e.message === 'Belief graph not found' && $runStatus === 'running'
      errorMsg = initializing ? '' : e.message
      if (!graph || !initializing) {
        graph = null
        stats = null
        promptContext = ''
      }
    } finally {
      loading = false
    }
  }

  function nodeColor(n) {
    if (n.kind === 'hypothesis') {
      return COLOR[`hypothesis_${n.status}`] || COLOR.hypothesis_active
    }
    if (n.kind === 'evidence') {
      return COLOR[`evidence_${n.polarity}`] || COLOR.evidence_neutral
    }
    if (n.kind === 'frontier') {
      return COLOR[`frontier_${n.state}`] || COLOR.frontier_proposed
    }
    return COLOR[n.kind] || COLOR.attempt
  }

  function nodeRadius(n) {
    if (n.kind === 'target') return 22
    if (n.kind === 'hypothesis') return 12 + 12 * (n.confidence ?? 0.5)
    if (n.kind === 'frontier') {
      const u = Math.max(0, Math.min(1, n.utility ?? 0))
      return 7 + 8 * u
    }
    if (n.kind === 'evidence') return 5
    if (n.kind === 'attempt') return 4
    if (n.kind === 'strategy') return 6
    return 6
  }

  function nodeShape(n) {
    if (n.kind === 'frontier') return d3.symbolSquare
    if (n.kind === 'evidence') return d3.symbolTriangle
    if (n.kind === 'strategy') return d3.symbolDiamond
    if (n.kind === 'target') return d3.symbolStar
    if (n.kind === 'attempt') return d3.symbolCircle
    return d3.symbolCircle
  }

  function edgeColor(e) {
    if (e.kind === 'hypothesis_supported_by_evidence') return 'var(--phosphor)'
    if (e.kind === 'hypothesis_refuted_by_evidence') return 'var(--red)'
    if (e.kind === 'frontier_expands_hypothesis') return 'var(--amber, #d4a017)'
    if (e.kind === 'attempt_tests_hypothesis') return 'var(--text-muted)'
    if (e.kind === 'attempt_used_strategy') return '#a78bfa'
    if (e.kind === 'attempt_observed_evidence') return 'var(--text-muted)'
    return 'var(--text-muted)'
  }

  // ---- humanized labels (replaces ID-prefixed ones) ----
  // The shape + color already carry the kind. The label exists to tell
  // the operator WHICH thing this node is — semantically — at a glance.
  // Full IDs stay reachable through the SVG <title> tooltip.
  function compactText(s, max) {
    const t = String(s || '').trim()
    if (!t) return ''
    return t.length <= max ? t : `${t.slice(0, max - 1)}…`
  }

  function labelFor(n) {
    if (n.kind === 'hypothesis') {
      const claim = compactText(n.claim, 30)
      const c = n.confidence !== undefined ? `${Math.round(n.confidence * 100)}%` : ''
      return claim ? (c ? `${claim} · ${c}` : claim) : c || 'hypothesis'
    }
    if (n.kind === 'frontier') {
      const u = n.utility !== undefined ? `u=${n.utility.toFixed(2)}` : ''
      return n.module ? (u ? `${n.module} · ${u}` : n.module) : 'frontier'
    }
    if (n.kind === 'evidence') {
      const sig = compactText(n.signal_type || '', 24).replaceAll('_', ' ')
      return n.polarity ? `${n.polarity} · ${sig || 'evidence'}` : sig || 'evidence'
    }
    if (n.kind === 'strategy') return n.family || 'strategy'
    if (n.kind === 'attempt') return n.module || 'attempt'
    if (n.kind === 'target') return n.target_hash ? n.target_hash.slice(0, 8) : 'target'
    return n.kind
  }

  function pickNode(n) {
    selectedBeliefNode.set(n)
  }

  function render() {
    if (!graph || !svgEl) return
    const rect = canvasEl?.getBoundingClientRect()
    if (rect) {
      width = rect.width
      height = rect.height
    }

    const svg = d3.select(svgEl)
    svg.selectAll('*').remove()
    svg.attr('viewBox', [0, 0, width, height])

    // Background-click clears selection. Node clicks call stopPropagation
    // so this only fires for clicks that actually hit the SVG itself.
    svg.on('click', (event) => {
      if (event.target === svgEl) selectedBeliefNode.set(null)
    })

    // Read latest selection so the .selected class survives the
    // tear-down/rebuild that load() → render() does on each poll. The
    // reactive-block sync below only re-fires on $selectedBeliefNode
    // changes; it can't recover the highlight if we just nuked the SVG.
    const selectedId = get(selectedBeliefNode)?.id ?? null

    const defs = svg.append('defs')
    defs
      .append('marker')
      .attr('id', 'belief-arrow')
      .attr('viewBox', '-0 -5 10 10')
      .attr('refX', 14)
      .attr('refY', 0)
      .attr('orient', 'auto')
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .append('path')
      .attr('d', 'M 0,-5 L 10,0 L 0,5')
      .attr('fill', 'var(--text-muted)')

    const root = svg.append('g').attr('class', 'belief-root')

    zoomBehavior = d3
      .zoom()
      .scaleExtent([0.2, 4])
      .on('zoom', (ev) => root.attr('transform', ev.transform))
    svg.call(zoomBehavior)

    const nodes = (graph.nodes || []).map((n) => ({ ...n }))
    const idIndex = new Map(nodes.map((n) => [n.id, n]))
    const links = (graph.edges || [])
      .filter((e) => idIndex.has(e.src_id) && idIndex.has(e.dst_id))
      .map((e) => ({ ...e, source: e.src_id, target: e.dst_id }))

    if (nodes.length === 0) {
      root
        .append('text')
        .attr('class', 'empty-msg')
        .attr('x', width / 2)
        .attr('y', height / 2)
        .attr('text-anchor', 'middle')
        .attr('fill', 'var(--text-muted)')
        .text('Belief graph is empty — run a scenario to populate it.')
      return
    }

    simulation = d3
      .forceSimulation(nodes)
      .force(
        'link',
        d3
          .forceLink(links)
          .id((d) => d.id)
          .distance((l) => {
            if (l.kind === 'frontier_expands_hypothesis') return 80
            if (l.kind === 'attempt_tests_hypothesis') return 110
            return 60
          })
          .strength(0.6),
      )
      .force('charge', d3.forceManyBody().strength(-260))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force(
        'collide',
        d3.forceCollide((d) => nodeRadius(d) + 6),
      )

    const linkSel = root
      .append('g')
      .attr('class', 'links')
      .attr('stroke-opacity', 0.55)
      .selectAll('line')
      .data(links)
      .enter()
      .append('line')
      .attr('class', (d) => `link link-${d.kind}`)
      .attr('stroke', edgeColor)
      .attr('stroke-width', (d) => 1 + (d.weight ?? 0) * 4)
      .attr('marker-end', 'url(#belief-arrow)')

    const symbol = d3.symbol()
    const nodeSel = root
      .append('g')
      .attr('class', 'nodes')
      .selectAll('g')
      .data(nodes)
      .enter()
      .append('g')
      .attr(
        'class',
        (d) =>
          `node node-${d.kind} status-${d.status || ''} state-${d.state || ''}${
            d.id === selectedId ? ' selected' : ''
          }`,
      )
      .style('cursor', 'pointer')
      .on('click', (ev, d) => {
        ev.stopPropagation()
        pickNode(d)
      })
      .call(
        d3
          .drag()
          .on('start', (ev, d) => {
            if (!ev.active) simulation.alphaTarget(0.3).restart()
            d.fx = d.x
            d.fy = d.y
          })
          .on('drag', (ev, d) => {
            d.fx = ev.x
            d.fy = ev.y
          })
          .on('end', (ev, d) => {
            if (!ev.active) simulation.alphaTarget(0)
            d.fx = null
            d.fy = null
          }),
      )

    nodeSel
      .append('path')
      .attr('d', (d) =>
        symbol.type(nodeShape(d)).size(Math.max(60, nodeRadius(d) * nodeRadius(d) * 4))(),
      )
      .attr('fill', nodeColor)
      .attr('stroke', 'rgba(255, 255, 255, 0.25)')
      .attr('stroke-width', 1.5)
      .attr('opacity', (d) => (d.status === 'stale' || d.state === 'dropped' ? 0.4 : 1))

    // Native SVG tooltip carries the full id + claim/instruction. Keeps
    // forensic detail within reach without putting the id on the canvas.
    nodeSel.append('title').text((d) => {
      const parts = [`${d.kind} · ${d.id}`]
      if (d.claim) parts.push(d.claim)
      else if (d.instruction) parts.push(d.instruction)
      else if (d.verbatim_fragment) parts.push(d.verbatim_fragment)
      else if (d.template_summary) parts.push(d.template_summary)
      return parts.join('\n')
    })

    nodeSel
      .append('text')
      .attr('class', 'node-label')
      .attr('dy', (d) => nodeRadius(d) + 12)
      .attr('text-anchor', 'middle')
      .attr('fill', 'var(--text)')
      .style('font-size', '11px')
      .style('font-family', 'var(--mono)')
      .style('pointer-events', 'none')
      .text((d) => labelFor(d))

    simulation.on('tick', () => {
      linkSel
        .attr('x1', (d) => d.source.x)
        .attr('y1', (d) => d.source.y)
        .attr('x2', (d) => d.target.x)
        .attr('y2', (d) => d.target.y)
      nodeSel.attr('transform', (d) => `translate(${d.x},${d.y})`)
    })
  }

  // ---- selection-driven highlight (no full re-render) ----
  // Following AttackGraph.svelte's pattern: tearing down the SVG would
  // break d3.zoom; instead toggle a class on the existing nodes.
  $: if (svgEl) {
    const id = $selectedBeliefNode?.id ?? null
    d3.select(svgEl)
      .selectAll('g.node')
      .classed('selected', (d) => d?.id === id)
  }

  onMount(() => {
    load()
    pollTimer = setInterval(() => {
      if (targetHash && $runStatus === 'running') {
        load()
      }
    }, 2500)
    const ro = new ResizeObserver(() => render())
    if (canvasEl) ro.observe(canvasEl)
    return () => {
      if (pollTimer) clearInterval(pollTimer)
      ro.disconnect()
    }
  })

  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer)
    simulation?.stop()
    selectedBeliefNode.set(null)
  })

  function refresh() {
    load()
  }

  function compact(text, max = 86) {
    const s = String(text || '').trim()
    if (!s) return '—'
    if (s.length <= max) return s
    return `${s.slice(0, max - 1)}…`
  }
</script>

<div class="belief-container" bind:this={containerEl}>
  <div class="belief-body">
    <!-- Canvas — full height. Container ref drives the ResizeObserver.
         Chrome + toolbar are positioned INSIDE the canvas so they clip
         to its width: when the inspector rail opens, the strip and
         stats stay over the graph and don't bleed over the rail tabs. -->
    <div class="canvas" bind:this={canvasEl}>
      <!-- chrome strip mirrors AttackGraph.svelte:319-325 so the two
           views read as the same product. Stats fold into the strip as
           a chip row (no separate band). -->
      <div class="graph-chrome">
        <span class="chrome-dot live" aria-hidden="true"></span>
        <span class="chrome-dot" aria-hidden="true"></span>
        <span class="chrome-dot" aria-hidden="true"></span>
        <span class="chrome-label">belief map ▸ live</span>
        {#if stats}
          <div class="chrome-stats">
            <span class="stat"><span class="stat-k">hyp</span><span class="stat-v">{stats.hypothesis ?? 0}<span class="dim">/{stats.active_hypotheses ?? 0}</span></span></span>
            <span class="stat"><span class="stat-k">ev</span><span class="stat-v">{stats.evidence ?? 0}</span></span>
            <span class="stat"><span class="stat-k">fx</span><span class="stat-v">{stats.frontier ?? 0}<span class="dim">/{stats.proposed_frontier ?? 0}</span></span></span>
            <span class="stat"><span class="stat-k">at</span><span class="stat-v">{stats.attempt ?? 0}</span></span>
          </div>
        {/if}
      </div>

      <!-- toolbar pill mirrors AttackGraph.svelte:344-348 — same pixel
           buttons, same hover treatment. -->
      <div class="toolbar">
        <button on:click={refresh}>Refresh</button>
        <button on:click={() => (inspectorOpen = !inspectorOpen)}>
          {inspectorOpen ? 'Hide rail' : 'Show rail'}
        </button>
      </div>
      {#if loading}
        <div class="overlay-status">Loading…</div>
      {:else if errorMsg}
        <div class="overlay-status error">{errorMsg}</div>
      {:else if !graph}
        <div class="overlay-status">
          {#if targetHash && $runStatus === 'running'}
            Belief graph is initializing…
          {:else if targetHash}
            No belief graph saved for this target yet.
          {:else}
            Pick a target to see its belief graph.
          {/if}
        </div>
      {/if}
      <svg bind:this={svgEl} class="belief-svg" width="100%" height="100%"></svg>

      <!-- Legend: bottom-right, matches AttackGraph.svelte:350-354 in
           position + styling. Toggleable so it never permanently obstructs
           the canvas. -->
      <div class="legend" class:collapsed={!legendOpen}>
        <button
          class="legend-toggle"
          on:click={() => (legendOpen = !legendOpen)}
          aria-label={legendOpen ? 'Hide legend' : 'Show legend'}
          title={legendOpen ? 'Hide legend' : 'Show legend'}
        >
          {#if legendOpen}—{:else}?{/if}
        </button>
        {#if legendOpen}
          <div class="legend-body">
            <div class="legend-section">
              <div class="legend-title">Nodes</div>
              <div class="li"><svg width="14" height="14" viewBox="-7 -7 14 14"><circle r="5" fill="var(--text)"/></svg>Hypothesis</div>
              <div class="li"><svg width="14" height="14" viewBox="-7 -7 14 14"><polygon points="0,-6 6,5 -6,5" fill="var(--phosphor)"/></svg>Evidence</div>
              <div class="li"><svg width="14" height="14" viewBox="-7 -7 14 14"><rect x="-5" y="-5" width="10" height="10" fill="var(--amber, #d4a017)"/></svg>Frontier</div>
              <div class="li"><svg width="14" height="14" viewBox="-7 -7 14 14"><polygon points="0,-6 6,0 0,6 -6,0" fill="#a78bfa"/></svg>Strategy</div>
              <div class="li"><svg width="14" height="14" viewBox="-7 -7 14 14"><polygon points="0,-6 1.8,-1.8 6,-1.8 2.6,1.4 4,6 0,3.2 -4,6 -2.6,1.4 -6,-1.8 -1.8,-1.8" fill="var(--phosphor)"/></svg>Target</div>
              <div class="li"><svg width="14" height="14" viewBox="-7 -7 14 14"><circle r="2.5" fill="var(--text-muted)"/></svg>Attempt</div>
            </div>
            <div class="legend-section">
              <div class="legend-title">Edges</div>
              <div class="li"><span class="edge-line phos"></span>supports</div>
              <div class="li"><span class="edge-line red"></span>refutes</div>
              <div class="li"><span class="edge-line amber"></span>expands</div>
              <div class="li"><span class="edge-line muted"></span>audit</div>
            </div>
          </div>
        {/if}
      </div>
    </div>

    <!-- Right inspector rail: collapsible; tabs for the three lists.
         When a node is selected, BeliefNodeDetail overlays this rail
         so the tab + list state survives the close. -->
    <aside class="inspector" class:open={inspectorOpen}>
      <button
        type="button"
        class="rail-toggle"
        class:open={inspectorOpen}
        on:click={() => (inspectorOpen = !inspectorOpen)}
        aria-label={inspectorOpen ? 'Collapse inspector' : 'Expand inspector'}
        title={inspectorOpen ? 'Collapse inspector' : 'Expand inspector'}
      >
        <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true">
          <polyline points="6,3 11,8 6,13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>

      {#if inspectorOpen}
        <div class="rail-tabs" role="tablist" aria-label="Inspector lists">
          <button
            class="rail-tab"
            class:active={inspectorTab === 'frontier'}
            role="tab"
            aria-selected={inspectorTab === 'frontier'}
            on:click={() => (inspectorTab = 'frontier')}
          >Frontier <span class="count">{frontierBoard.length}</span></button>
          <button
            class="rail-tab"
            class:active={inspectorTab === 'evidence'}
            role="tab"
            aria-selected={inspectorTab === 'evidence'}
            on:click={() => (inspectorTab = 'evidence')}
          >Evidence <span class="count">{evidenceTimeline.length}</span></button>
          <button
            class="rail-tab"
            class:active={inspectorTab === 'strategy'}
            role="tab"
            aria-selected={inspectorTab === 'strategy'}
            on:click={() => (inspectorTab = 'strategy')}
          >Strategy <span class="count">{strategyRows.length}</span></button>
        </div>

        <div class="rail-content">
          {#if inspectorTab === 'frontier'}
            {#if frontierBoard.length}
              {#each frontierBoard as f (f.id)}
                <button class="rail-row" on:click={() => pickNode(f)}>
                  <div class="row-head">
                    <span class="row-title">{f.module || 'frontier'}</span>
                    <span class="row-tag amber">u={(f.utility ?? 0).toFixed(2)}</span>
                  </div>
                  <div class="row-sub">{compact(f.instruction)}</div>
                </button>
              {/each}
            {:else}
              <div class="empty-row">No proposed experiments.</div>
            {/if}

          {:else if inspectorTab === 'evidence'}
            {#if evidenceTimeline.length}
              {#each evidenceTimeline as ev (ev.id)}
                <button class="rail-row" on:click={() => pickNode(ev)}>
                  <div class="row-head">
                    <span class="row-title">{(ev.signal_type || 'evidence').replaceAll('_', ' ')}</span>
                    <span class="row-tag {ev.polarity}">{ev.polarity || 'neutral'}</span>
                  </div>
                  <div class="row-sub">Δ={(ev.confidence_delta ?? 0).toFixed(2)} · {compact(ev.verbatim_fragment)}</div>
                </button>
              {/each}
            {:else}
              <div class="empty-row">No evidence extracted yet.</div>
            {/if}

          {:else if inspectorTab === 'strategy'}
            {#if strategyRows.length}
              {#each strategyRows as st (st.id)}
                <button class="rail-row" on:click={() => pickNode(st)}>
                  <div class="row-head">
                    <span class="row-title">{st.family || 'strategy'}</span>
                    <span class="row-tag muted">{st.success_count}/{st.attempt_count}</span>
                  </div>
                  <div class="row-sub">{compact(st.template_summary)}</div>
                </button>
              {/each}
            {:else}
              <div class="empty-row">No local strategies yet.</div>
            {/if}
          {/if}
        </div>

        <!-- Overlay detail: takes over the rail when a node is selected.
             Closing it (BeliefNodeDetail's × button → store=null) returns
             the operator to whichever list tab they had active. -->
        {#if $selectedBeliefNode}
          <div class="rail-overlay">
            <BeliefNodeDetail {promptContext} />
          </div>
        {/if}
      {/if}
    </aside>
  </div>
</div>

<style>
  .belief-container {
    position: relative;
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
    background: var(--bg-primary);
    color: var(--text);
    overflow: hidden;
  }

  /* ---------- chrome strip (matches AttackGraph.svelte:367-406) ---------- */
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
  .chrome-stats {
    margin-left: auto;
    margin-right: 4px;
    display: flex;
    gap: 8px;
    pointer-events: auto;
  }
  .stat {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 7px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 3px;
    font-family: var(--mono);
    font-size: 10px;
  }
  .stat-k {
    color: var(--text-muted);
    text-transform: uppercase;
    font-size: 9px;
    letter-spacing: 0.06em;
  }
  .stat-v { color: var(--text); }
  .stat-v .dim { color: var(--text-muted); }

  /* ---------- toolbar (matches AttackGraph.svelte:518-543) ---------- */
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

  /* ---------- body: canvas + inspector rail ---------- */
  .belief-body {
    flex: 1;
    display: flex;
    min-height: 0;
  }
  .canvas {
    position: relative;
    flex: 1;
    overflow: hidden;
    background: var(--bg-primary);
  }
  .belief-svg {
    display: block;
    width: 100%;
    height: 100%;
  }

  .overlay-status {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
    pointer-events: none;
    font-size: 0.9rem;
  }
  .overlay-status.error { color: var(--red); }

  /* ---------- canvas node treatments (selected highlight) ---------- */
  .belief-container :global(.node) {
    cursor: pointer;
  }
  .belief-container :global(.node path) {
    transition: stroke 120ms;
  }
  .belief-container :global(.node:hover path) {
    stroke: rgba(255, 255, 255, 0.55);
    stroke-width: 2;
  }
  .belief-container :global(.node.selected path) {
    stroke: var(--accent);
    stroke-width: 3.5px;
    filter: drop-shadow(0 0 8px var(--accent));
  }

  /* ---------- legend (bottom-right, matches AttackGraph) ---------- */
  .legend {
    position: absolute;
    bottom: 12px;
    right: 12px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 6px 8px;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    z-index: 6;
    box-shadow: 0 2px 8px hsla(0 0% 0% / 0.4);
  }
  .legend.collapsed {
    padding: 2px;
  }
  .legend-toggle {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-muted);
    width: 20px;
    height: 20px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 0.75rem;
    line-height: 1;
    padding: 0;
    position: absolute;
    top: 4px;
    right: 4px;
  }
  .legend-toggle:hover {
    color: var(--phosphor);
    border-color: var(--phosphor);
  }
  .legend.collapsed .legend-toggle {
    position: static;
  }
  .legend-body {
    display: flex;
    gap: 14px;
    padding-right: 24px;
  }
  .legend-section { display: flex; flex-direction: column; gap: 4px; }
  .legend-title {
    color: var(--phosphor);
    font-size: 0.5625rem;
    margin-bottom: 2px;
  }
  .legend .li {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--text);
    text-transform: none;
    font-family: var(--mono);
    font-size: 0.6875rem;
    letter-spacing: 0;
  }
  .edge-line {
    display: inline-block;
    width: 14px;
    height: 2px;
    border-radius: 1px;
  }
  .edge-line.phos { background: var(--phosphor); box-shadow: var(--phosphor-glow-tight); }
  .edge-line.red { background: var(--red); }
  .edge-line.amber { background: var(--amber, #d4a017); }
  .edge-line.muted { background: var(--text-muted); }

  /* ---------- inspector rail ---------- */
  .inspector {
    position: relative;
    flex-shrink: 0;
    width: 0;
    overflow: hidden;
    background: var(--bg-secondary);
    border-left: 1px solid var(--border);
    transition: width 0.2s ease;
    display: flex;
    flex-direction: column;
  }
  .inspector.open {
    width: 360px;
  }

  /* Rail edge-toggle, mirrors App.svelte's .edge-toggle pattern. */
  .rail-toggle {
    position: absolute;
    top: 50%;
    left: -1px;
    transform: translate(-100%, -50%);
    width: 18px;
    height: 56px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-right: none;
    border-radius: 6px 0 0 6px;
    color: var(--text-muted);
    cursor: pointer;
    padding: 0;
    z-index: 50;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: color 0.15s, border-color 0.15s, background 0.15s;
  }
  .rail-toggle:hover {
    color: var(--accent);
    border-color: var(--accent);
    background: var(--bg-secondary);
    box-shadow: var(--phosphor-glow-tight);
  }
  .rail-toggle svg {
    transition: transform 0.2s ease;
  }
  .rail-toggle.open svg {
    transform: rotate(0deg);
  }
  .rail-toggle:not(.open) svg {
    transform: rotate(180deg);
  }

  /* When rail is closed, surface the toggle on the right edge of the
     canvas instead of dangling off a 0-width sidebar. */
  .inspector:not(.open) .rail-toggle {
    left: auto;
    right: 0;
    transform: translate(0, -50%);
    border-right: 1px solid var(--border);
    border-left: none;
    border-radius: 6px 0 0 6px;
  }

  .rail-tabs {
    flex-shrink: 0;
    display: flex;
    gap: 2px;
    padding: 8px 10px 0;
    border-bottom: 1px solid var(--border);
  }
  .rail-tab {
    flex: 1;
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 7px 6px;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    transition: color 120ms, border-color 120ms;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
  }
  .rail-tab:hover { color: var(--text); }
  .rail-tab.active {
    color: var(--phosphor);
    border-bottom-color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
  }
  .rail-tab .count {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text-muted);
    border-radius: 3px;
    font-size: 0.625rem;
    padding: 1px 5px;
    text-shadow: none;
    font-family: var(--mono);
    letter-spacing: 0;
  }
  .rail-tab.active .count {
    color: var(--phosphor);
    border-color: var(--phosphor);
  }

  .rail-content {
    flex: 1;
    overflow-y: auto;
    padding: 6px 8px 16px;
  }
  .rail-row {
    width: 100%;
    display: block;
    text-align: left;
    background: transparent;
    border: 1px solid transparent;
    color: var(--text);
    padding: 7px 8px;
    margin-bottom: 4px;
    border-radius: 4px;
    cursor: pointer;
    transition: background 120ms, border-color 120ms;
  }
  .rail-row:hover {
    background: color-mix(in srgb, var(--phosphor) 6%, transparent);
    border-color: var(--border);
  }
  .row-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 3px;
  }
  .row-title {
    font-family: var(--mono);
    font-size: 0.78rem;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }
  .row-tag {
    flex-shrink: 0;
    font-family: var(--font-pixel);
    font-size: 0.5625rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 2px 6px;
    border-radius: 3px;
    border: 1px solid var(--border);
    color: var(--text-muted);
    background: var(--bg-tertiary);
  }
  .row-tag.amber {
    color: var(--amber, #d4a017);
    border-color: var(--amber, #d4a017);
    background: rgba(245, 158, 11, 0.08);
  }
  .row-tag.supports {
    color: var(--phosphor);
    border-color: var(--phosphor);
    background: hsla(155 100% 42% / 0.10);
  }
  .row-tag.refutes {
    color: var(--red);
    border-color: var(--red);
    background: rgba(239, 68, 68, 0.10);
  }
  .row-tag.muted {
    color: var(--text-muted);
  }
  .row-sub {
    color: var(--text-muted);
    font-size: 0.7rem;
    line-height: 1.35;
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }
  .empty-row {
    color: var(--text-muted);
    font-size: 0.75rem;
    padding: 12px 8px;
    text-align: center;
  }

  .rail-overlay {
    position: absolute;
    inset: 0;
    background: var(--bg-secondary);
    z-index: 12;
    display: flex;
    flex-direction: column;
  }
</style>
