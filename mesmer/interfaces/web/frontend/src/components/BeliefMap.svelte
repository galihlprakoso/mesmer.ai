<script>
  /**
   * Belief Attack Graph view.
   *
   * Renders the typed planner state as a force-directed layout:
   *   - WeaknessHypothesis nodes (large circles, sized by confidence)
   *     occupy the center.
   *   - Evidence nodes (small triangles) orbit their hypothesis with
   *     a support/refute color cue.
   *   - FrontierExperiment nodes (squares) sit near the hypothesis
   *     they expand, sized by utility.
   *   - Strategy nodes (small diamonds) link out from attempts that
   *     used them.
   *   - Attempt nodes (dots) are de-emphasised — they're the audit
   *     trail, not the planner state.
   *
   * Distinct from `AttackGraph.svelte` which renders the legacy
   * `AttackGraph` as a hierarchical tree of attempt history. The
   * belief map answers "what does the planner believe right now?";
   * the attack graph answers "what did we try?".
   */
  import { onDestroy, onMount } from 'svelte'
  import * as d3 from 'd3'
  import { getBeliefGraph } from '../lib/api.js'

  /** @type {string|null} */
  export let targetHash = null

  let containerEl
  let svgEl
  let width = 800
  let height = 600

  let loading = false
  let errorMsg = ''
  let graph = null
  let stats = null
  let promptContext = ''
  let selectedNode = null
  let simulation = null
  let zoomBehavior = null

  // Color palette aligned with AttackGraph.svelte to keep the two
  // views visually consistent. Status semantics mostly transfer:
  // confirmed/promising = phosphor green, refuted/dead = red, etc.
  const COLOR = {
    hypothesis_active: 'var(--text)',
    hypothesis_confirmed: 'var(--phosphor)',
    hypothesis_refuted: 'var(--red)',
    hypothesis_stale: 'var(--text-muted)',
    evidence_supports: 'var(--phosphor)',
    evidence_refutes: 'var(--red)',
    evidence_neutral: 'var(--text-muted)',
    frontier_proposed: 'var(--accent, #d4a017)',
    frontier_executing: 'var(--accent, #d4a017)',
    frontier_fulfilled: 'var(--text-muted)',
    frontier_dropped: 'var(--text-muted)',
    strategy: '#7e57c2',
    attempt: 'var(--text-muted)',
    target: 'var(--text)',
  }

  $: void targetHash, void load()
  $: graphNodes = graph?.nodes || []
  $: frontierBoard = graphNodes
    .filter((n) => n.kind === 'frontier' && n.state === 'proposed')
    .slice()
    .sort((a, b) => (b.utility ?? 0) - (a.utility ?? 0))
    .slice(0, 8)
  $: evidenceTimeline = graphNodes
    .filter((n) => n.kind === 'evidence')
    .slice()
    .sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0))
    .slice(0, 8)
  $: strategyRows = graphNodes
    .filter((n) => n.kind === 'strategy')
    .slice()
    .sort((a, b) => {
      const ar = a.attempt_count ? a.success_count / a.attempt_count : 0
      const br = b.attempt_count ? b.success_count / b.attempt_count : 0
      return br - ar
    })
    .slice(0, 8)

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
      // Defer render to allow Svelte to commit svgEl before we
      // measure container dimensions.
      requestAnimationFrame(render)
    } catch (e) {
      errorMsg = e.message
      graph = null
      stats = null
      promptContext = ''
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
    if (n.kind === 'hypothesis') {
      // Sized by confidence — louder hypotheses pop visually.
      return 12 + 12 * (n.confidence ?? 0.5)
    }
    if (n.kind === 'frontier') {
      // Sized by utility (clamped to non-negative for radius).
      const u = Math.max(0, Math.min(1, n.utility ?? 0))
      return 7 + 8 * u
    }
    if (n.kind === 'evidence') return 5
    if (n.kind === 'attempt') return 4
    if (n.kind === 'strategy') return 6
    return 6
  }

  function nodeShape(n) {
    // d3.symbolType — picked to make the kind readable at a glance.
    if (n.kind === 'frontier') return d3.symbolSquare
    if (n.kind === 'evidence') return d3.symbolTriangle
    if (n.kind === 'strategy') return d3.symbolDiamond
    if (n.kind === 'target') return d3.symbolStar
    if (n.kind === 'attempt') return d3.symbolCircle
    return d3.symbolCircle
  }

  function edgeColor(e) {
    // Polarity carries through edge color so a refute relationship is
    // immediately readable as a red link to the evidence node.
    if (e.kind === 'hypothesis_supported_by_evidence') return 'var(--phosphor)'
    if (e.kind === 'hypothesis_refuted_by_evidence') return 'var(--red)'
    if (e.kind === 'frontier_expands_hypothesis') return 'var(--accent, #d4a017)'
    if (e.kind === 'attempt_tests_hypothesis') return 'var(--text-muted)'
    if (e.kind === 'attempt_used_strategy') return '#7e57c2'
    if (e.kind === 'attempt_observed_evidence') return 'var(--text-muted)'
    return 'var(--text-muted)'
  }

  function render() {
    if (!graph || !svgEl) return
    const rect = containerEl?.getBoundingClientRect()
    if (rect) {
      width = rect.width
      height = rect.height
    }

    const svg = d3.select(svgEl)
    svg.selectAll('*').remove()
    svg.attr('viewBox', [0, 0, width, height])

    // Defs — arrow marker for directed edges.
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

    // d3-force takes plain mutable objects; clone so we don't poison
    // the prop. Edges reference node ids; rebuild linkable shape.
    const nodes = (graph.nodes || []).map((n) => ({ ...n }))
    const idIndex = new Map(nodes.map((n) => [n.id, n]))
    const links = (graph.edges || [])
      .filter((e) => idIndex.has(e.src_id) && idIndex.has(e.dst_id))
      .map((e) => ({
        ...e,
        source: e.src_id,
        target: e.dst_id,
      }))

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
      .attr('class', (d) => `node node-${d.kind} status-${d.status || ''} state-${d.state || ''}`)
      .style('cursor', 'pointer')
      .on('click', (_, d) => {
        selectedNode = d
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
      .attr('stroke', 'var(--text)')
      .attr('stroke-width', 1)
      .attr('opacity', (d) => (d.status === 'stale' || d.state === 'dropped' ? 0.4 : 1))

    nodeSel
      .append('text')
      .attr('class', 'node-label')
      .attr('dy', (d) => nodeRadius(d) + 12)
      .attr('text-anchor', 'middle')
      .attr('fill', 'var(--text)')
      .style('font-size', '11px')
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

  function labelFor(n) {
    if (n.kind === 'hypothesis') {
      const conf = n.confidence !== undefined ? `${n.confidence.toFixed(2)}` : '?'
      return `${n.id} · ${conf}`
    }
    if (n.kind === 'frontier') {
      const u = n.utility !== undefined ? `u=${n.utility.toFixed(2)}` : ''
      return `${n.id} · ${u}`
    }
    if (n.kind === 'target') return n.target_hash ? n.target_hash.slice(0, 8) : 'target'
    return n.id
  }

  function detailsFor(n) {
    if (!n) return null
    if (n.kind === 'hypothesis') {
      return [
        ['family', n.family],
        ['confidence', (n.confidence ?? 0).toFixed(3)],
        ['status', n.status],
        ['claim', n.claim],
        ['description', n.description || '—'],
      ]
    }
    if (n.kind === 'evidence') {
      return [
        ['signal_type', n.signal_type],
        ['polarity', n.polarity],
        ['hypothesis_id', n.hypothesis_id || '—'],
        ['Δ', (n.confidence_delta ?? 0).toFixed(3)],
        ['fragment', n.verbatim_fragment],
        ['rationale', n.rationale],
      ]
    }
    if (n.kind === 'frontier') {
      return [
        ['module', n.module],
        ['state', n.state],
        ['utility', (n.utility ?? 0).toFixed(3)],
        ['hypothesis_id', n.hypothesis_id],
        ['strategy_id', n.strategy_id || '—'],
        ['instruction', n.instruction],
        ['expected_signal', n.expected_signal],
      ]
    }
    if (n.kind === 'attempt') {
      return [
        ['module', n.module],
        ['outcome', n.outcome || '—'],
        ['judge_score', String(n.judge_score ?? '—')],
        ['experiment_id', n.experiment_id || '—'],
        ['tested_hypothesis_ids', (n.tested_hypothesis_ids || []).join(', ') || '—'],
      ]
    }
    if (n.kind === 'strategy') {
      const rate = n.attempt_count ? (n.success_count / n.attempt_count).toFixed(2) : '—'
      return [
        ['family', n.family],
        ['template_summary', n.template_summary],
        ['success_rate', `${rate} (${n.success_count}/${n.attempt_count})`],
      ]
    }
    if (n.kind === 'target') {
      return [
        ['target_hash', n.target_hash],
        ['traits', JSON.stringify(n.traits || {}, null, 2)],
      ]
    }
    return [['kind', n.kind], ['id', n.id]]
  }

  onMount(() => {
    load()
    const ro = new ResizeObserver(() => render())
    if (containerEl) ro.observe(containerEl)
    return () => ro.disconnect()
  })

  onDestroy(() => {
    simulation?.stop()
  })

  function refresh() {
    load()
  }

  function compact(text, max = 120) {
    const s = String(text || '').trim()
    if (!s) return '—'
    if (s.length <= max) return s
    return `${s.slice(0, max - 1)}…`
  }
</script>

<div class="belief-map" bind:this={containerEl}>
  <div class="header">
    <div class="title">Belief Attack Graph</div>
    <button class="refresh" on:click={refresh}>Refresh</button>
  </div>

  {#if loading}
    <div class="status">Loading…</div>
  {:else if errorMsg}
    <div class="status error">{errorMsg}</div>
  {:else if !graph}
    <div class="status">Pick a target to see its belief graph.</div>
  {:else}
    <div class="stats">
      {#if stats}
        <span>hypotheses: {stats.hypothesis ?? 0} ({stats.active_hypotheses ?? 0} active)</span>
        <span>evidence: {stats.evidence ?? 0}</span>
        <span>frontier: {stats.frontier ?? 0} ({stats.proposed_frontier ?? 0} proposed)</span>
        <span>attempts: {stats.attempt ?? 0}</span>
        <span>edges: {stats.edges ?? 0}</span>
      {/if}
    </div>
    <div class="boards">
      <section class="board">
        <div class="board-title">Frontier Board</div>
        {#if frontierBoard.length}
          {#each frontierBoard as f (f.id)}
            <button class="board-row" on:click={() => (selectedNode = f)}>
              <span class="row-main">{f.id} · {f.module}</span>
              <span class="row-sub">u={(f.utility ?? 0).toFixed(2)} · {compact(f.instruction, 86)}</span>
            </button>
          {/each}
        {:else}
          <div class="empty-row">No proposed experiments.</div>
        {/if}
      </section>

      <section class="board">
        <div class="board-title">Evidence Timeline</div>
        {#if evidenceTimeline.length}
          {#each evidenceTimeline as ev (ev.id)}
            <button class="board-row" on:click={() => (selectedNode = ev)}>
              <span class="row-main">{ev.id} · {ev.signal_type} · {ev.polarity}</span>
              <span class="row-sub">Δ={(ev.confidence_delta ?? 0).toFixed(2)} · {compact(ev.verbatim_fragment, 86)}</span>
            </button>
          {/each}
        {:else}
          <div class="empty-row">No evidence extracted yet.</div>
        {/if}
      </section>

      <section class="board">
        <div class="board-title">Strategy Library</div>
        {#if strategyRows.length}
          {#each strategyRows as st (st.id)}
            <button class="board-row" on:click={() => (selectedNode = st)}>
              <span class="row-main">{st.id} · {st.family}</span>
              <span class="row-sub">{st.success_count}/{st.attempt_count} · {compact(st.template_summary, 86)}</span>
            </button>
          {/each}
        {:else}
          <div class="empty-row">No local strategies yet.</div>
        {/if}
      </section>
    </div>
    <div class="canvas">
      <svg bind:this={svgEl} class="belief-svg" width="100%" height="100%"></svg>
      {#if selectedNode}
        <aside class="detail">
          <div class="detail-head">
            <span class="kind">{selectedNode.kind}</span>
            <span class="id">{selectedNode.id}</span>
            <button class="close" on:click={() => (selectedNode = null)}>×</button>
          </div>
          <table>
            <tbody>
              {#each detailsFor(selectedNode) as [k, v]}
                <tr>
                  <td class="k">{k}</td>
                  <td class="v">{v}</td>
                </tr>
              {/each}
            </tbody>
          </table>
          {#if promptContext}
            <details class="prompt-context">
              <summary>Prompt Context</summary>
              <pre>{promptContext}</pre>
            </details>
          {/if}
        </aside>
      {/if}
    </div>
  {/if}
</div>

<style>
  .belief-map {
    display: flex;
    flex-direction: column;
    height: 100%;
    width: 100%;
    color: var(--text);
  }
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.5rem 1rem;
    border-bottom: 1px solid var(--border, #333);
  }
  .title {
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .refresh {
    background: transparent;
    color: var(--text);
    border: 1px solid var(--border, #333);
    padding: 0.2rem 0.6rem;
    cursor: pointer;
  }
  .refresh:hover {
    background: var(--surface-2, #181818);
  }
  .status {
    padding: 1rem;
    color: var(--text-muted);
  }
  .status.error {
    color: var(--red);
  }
  .stats {
    display: flex;
    gap: 1.2rem;
    flex-wrap: wrap;
    padding: 0.4rem 1rem;
    color: var(--text-muted);
    font-size: 0.85rem;
    border-bottom: 1px dashed var(--border, #333);
  }
  .boards {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.5rem;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px dashed var(--border, #333);
    background: var(--surface-1, #0d0d0d);
  }
  .board {
    min-width: 0;
    border: 1px solid var(--border, #333);
    background: var(--surface-2, #181818);
    padding: 0.45rem;
  }
  .board-title {
    color: var(--phosphor);
    font-size: 0.72rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 0.35rem;
  }
  .board-row {
    width: 100%;
    display: block;
    text-align: left;
    background: transparent;
    border: 0;
    border-top: 1px solid var(--border, #333);
    color: var(--text);
    padding: 0.35rem 0.15rem;
    cursor: pointer;
  }
  .board-row:hover {
    background: color-mix(in srgb, var(--phosphor) 8%, transparent);
  }
  .row-main,
  .row-sub {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .row-main {
    font-family: ui-monospace, monospace;
    font-size: 0.76rem;
  }
  .row-sub,
  .empty-row {
    color: var(--text-muted);
    font-size: 0.74rem;
    line-height: 1.25;
  }
  .empty-row {
    border-top: 1px solid var(--border, #333);
    padding: 0.45rem 0.15rem;
  }
  .canvas {
    position: relative;
    flex: 1;
    overflow: hidden;
  }
  .belief-svg {
    display: block;
    width: 100%;
    height: 100%;
    background: var(--surface-1, #0d0d0d);
  }
  .detail {
    position: absolute;
    right: 0.5rem;
    top: 0.5rem;
    width: 320px;
    max-height: calc(100% - 1rem);
    overflow: auto;
    background: var(--surface-2, #181818);
    border: 1px solid var(--border, #333);
    padding: 0.6rem;
    font-size: 0.85rem;
  }
  .detail-head {
    display: flex;
    gap: 0.6rem;
    align-items: baseline;
    margin-bottom: 0.5rem;
  }
  .detail-head .kind {
    text-transform: uppercase;
    color: var(--phosphor);
    font-size: 0.7rem;
    letter-spacing: 0.08em;
  }
  .detail-head .id {
    color: var(--text-muted);
    font-family: ui-monospace, monospace;
  }
  .detail-head .close {
    margin-left: auto;
    background: transparent;
    color: var(--text-muted);
    border: none;
    cursor: pointer;
    font-size: 1rem;
  }
  .detail table {
    width: 100%;
    border-collapse: collapse;
  }
  .detail td {
    padding: 0.2rem 0.3rem;
    vertical-align: top;
  }
  .detail td.k {
    color: var(--text-muted);
    width: 35%;
    font-family: ui-monospace, monospace;
    font-size: 0.75rem;
  }
  .detail td.v {
    color: var(--text);
    word-break: break-word;
    white-space: pre-wrap;
  }
  .prompt-context {
    margin-top: 0.75rem;
    border-top: 1px solid var(--border, #333);
    padding-top: 0.5rem;
  }
  .prompt-context summary {
    cursor: pointer;
    color: var(--phosphor);
    font-size: 0.78rem;
  }
  .prompt-context pre {
    max-height: 260px;
    overflow: auto;
    white-space: pre-wrap;
    color: var(--text-muted);
    font-size: 0.72rem;
  }
  @media (max-width: 980px) {
    .boards {
      grid-template-columns: 1fr;
      max-height: 260px;
      overflow: auto;
    }
  }
</style>
