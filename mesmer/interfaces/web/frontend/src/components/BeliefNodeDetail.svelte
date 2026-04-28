<script>
  /**
   * Right-rail detail panel for the Belief Map.
   *
   * Mirrors `NodeDetail.svelte`'s shell (kind badge → banners → tab-strip →
   * tab-content) but renders belief-graph node payloads, which carry a
   * different schema than legacy AttackNodes (claim / confidence / polarity
   * / utility / instruction / etc.).
   *
   * The component lives INSIDE `BeliefMap.svelte`'s inspector rail rather
   * than at the App level, so it adapts to the rail's current width and
   * doesn't reflow the whole graph viewport when selection toggles.
   *
   * Three tabs:
   *   - Summary: humanized prose for the most useful fields per kind
   *   - Raw: typed key/value rows for forensics (former `.detail` table)
   *   - Prompt: the LEADER brief currently injected into the agent
   */
  import { selectedBeliefNode } from '../lib/stores.js'

  /** @type {string} */
  export let promptContext = ''

  $: node = $selectedBeliefNode

  function close() {
    selectedBeliefNode.set(null)
  }

  function pct(v) {
    return `${Math.round((v ?? 0) * 100)}%`
  }

  function fixed(v, n = 3) {
    return Number(v ?? 0).toFixed(n)
  }

  function frontierComponents(n) {
    if (!n || n.kind !== 'frontier') return []
    return [
      ['expected progress', n.expected_progress],
      ['information gain', n.information_gain],
      ['hypothesis conf', n.hypothesis_confidence],
      ['novelty', n.novelty],
      ['strategy prior', n.strategy_prior],
      ['transfer value', n.transfer_value],
      ['query cost', n.query_cost],
      ['repeat penalty', n.repetition_penalty],
      ['dead similarity', n.dead_similarity],
    ]
  }

  // Kind-specific accent colors mirror the canvas palette so the badge
  // and the node a user just clicked read as the same thing.
  const KIND_COLOR = {
    hypothesis: 'var(--text)',
    evidence: 'var(--phosphor)',
    frontier: 'var(--amber, #d4a017)',
    strategy: '#a78bfa',
    attempt: 'var(--text-muted)',
    target: 'var(--phosphor)',
  }

  function tabsFor(n) {
    if (!n) return []
    const out = [{ key: 'summary', label: 'Summary' }, { key: 'raw', label: 'Raw' }]
    if (promptContext) out.push({ key: 'prompt', label: 'Prompt' })
    return out
  }

  let activeTab = 'summary'
  $: tabs = tabsFor(node)
  // Repair: if selection changes and current tab is gone, reset.
  $: if (tabs.length > 0 && !tabs.find((t) => t.key === activeTab)) {
    activeTab = tabs[0].key
  }

  // ---- Raw fields (typed key/value rows) ----
  function rawRows(n) {
    if (!n) return []
    if (n.kind === 'hypothesis') {
      return [
        ['family', n.family],
        ['confidence', fixed(n.confidence)],
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
        ['Δ', fixed(n.confidence_delta)],
        ['fragment', n.verbatim_fragment],
        ['rationale', n.rationale],
      ]
    }
    if (n.kind === 'frontier') {
      return [
        ['module', n.module],
        ['state', n.state],
        ['utility', fixed(n.utility)],
        ['expected_progress', fixed(n.expected_progress)],
        ['information_gain', fixed(n.information_gain)],
        ['hypothesis_confidence', fixed(n.hypothesis_confidence)],
        ['novelty', fixed(n.novelty)],
        ['strategy_prior', fixed(n.strategy_prior)],
        ['transfer_value', fixed(n.transfer_value)],
        ['transfer_source', n.transfer_source || '—'],
        ['transfer_success_rate', fixed(n.transfer_success_rate)],
        ['transfer_attempts', n.transfer_attempts ?? 0],
        ['query_cost', fixed(n.query_cost)],
        ['query_cost_reason', n.query_cost_reason || '—'],
        ['query_cost_tier', n.query_cost_tier ?? '—'],
        ['repetition_penalty', fixed(n.repetition_penalty)],
        ['dead_similarity', fixed(n.dead_similarity)],
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
        ['tested_hypotheses', (n.tested_hypothesis_ids || []).join(', ') || '—'],
      ]
    }
    if (n.kind === 'strategy') {
      const rate = n.attempt_count ? (n.success_count / n.attempt_count).toFixed(2) : '—'
      return [
        ['family', n.family],
        ['template', n.template_summary],
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
  $: rows = rawRows(node)
</script>

{#if node}
  <aside class="belief-detail">
    <div class="detail-header">
      <span class="kind-badge" style="--kind-color: {KIND_COLOR[node.kind] || 'var(--text)'}">{node.kind}</span>
      <span class="node-id" title={node.id}>{node.id}</span>
      <button class="close-btn" on:click={close} aria-label="Close">&times;</button>
    </div>

    <!-- Banners — single status pill driven by the most semantically
         meaningful field per kind. Mirrors NodeDetail's status-row. -->
    <div class="banners">
      {#if node.kind === 'hypothesis' && node.status}
        <span class="status-badge {node.status}">{node.status}</span>
      {:else if node.kind === 'frontier' && node.state}
        <span class="status-badge frontier-{node.state}">{node.state}</span>
      {:else if node.kind === 'evidence' && node.polarity}
        <span class="status-badge polarity-{node.polarity}">{node.polarity}</span>
      {:else if node.kind === 'attempt' && node.outcome}
        <span class="status-badge attempt-{node.outcome}">{node.outcome}</span>
      {/if}
    </div>

    <div class="tab-strip" role="tablist">
      {#each tabs as t (t.key)}
        <button
          class="tab"
          class:active={activeTab === t.key}
          role="tab"
          aria-selected={activeTab === t.key}
          on:click={() => (activeTab = t.key)}
        >{t.label}</button>
      {/each}
    </div>

    <div class="tab-content">
      {#if activeTab === 'summary'}
        {#if node.kind === 'hypothesis'}
          <div class="claim">{node.claim || '—'}</div>
          <div class="meta-row">
            <span class="meta-key">family</span>
            <span class="meta-val mono">{node.family || '—'}</span>
          </div>
          <div class="meta-row">
            <span class="meta-key">confidence</span>
            <span class="meta-val">
              <span class="bar"><span class="bar-fill" style="width: {pct(node.confidence)}"></span></span>
              <span class="mono">{pct(node.confidence)}</span>
            </span>
          </div>
          {#if node.description}
            <div class="prose">{node.description}</div>
          {/if}

        {:else if node.kind === 'evidence'}
          <div class="prose verbatim">{node.verbatim_fragment || '—'}</div>
          <div class="meta-row">
            <span class="meta-key">signal</span>
            <span class="meta-val mono">{node.signal_type || '—'}</span>
          </div>
          <div class="meta-row">
            <span class="meta-key">Δ confidence</span>
            <span class="meta-val mono">{fixed(node.confidence_delta, 2)}</span>
          </div>
          {#if node.hypothesis_id}
            <div class="meta-row">
              <span class="meta-key">hypothesis</span>
              <span class="meta-val mono">{node.hypothesis_id}</span>
            </div>
          {/if}
          {#if node.rationale}
            <div class="prose dim">{node.rationale}</div>
          {/if}

        {:else if node.kind === 'frontier'}
          <div class="meta-row">
            <span class="meta-key">module</span>
            <span class="meta-val mono">{node.module}</span>
          </div>
          <div class="meta-row">
            <span class="meta-key">utility</span>
            <span class="meta-val">
              <span class="bar"><span class="bar-fill amber" style="width: {pct(Math.max(0, Math.min(1, node.utility ?? 0)))}"></span></span>
              <span class="mono">{fixed(node.utility, 2)}</span>
            </span>
          </div>
          {#if node.instruction}
            <div class="prose">{node.instruction}</div>
          {/if}
          <div class="component-grid">
            {#each frontierComponents(node) as [label, value]}
              <div class="component">
                <span class="component-k">{label}</span>
                <span class="component-v mono">{fixed(value, 2)}</span>
              </div>
            {/each}
          </div>
          {#if node.transfer_source || node.query_cost_reason}
            <div class="prose dim">
              {#if node.transfer_source}
                Transfer: {node.transfer_source}
                ({fixed(node.transfer_success_rate, 2)}, {node.transfer_attempts ?? 0} attempts).
              {/if}
              {#if node.query_cost_reason}
                Cost: {node.query_cost_reason}.
              {/if}
            </div>
          {/if}
          {#if node.expected_signal}
            <div class="meta-row">
              <span class="meta-key">expected</span>
              <span class="meta-val">{node.expected_signal}</span>
            </div>
          {/if}
          {#if node.hypothesis_id}
            <div class="meta-row">
              <span class="meta-key">tests</span>
              <span class="meta-val mono">{node.hypothesis_id}</span>
            </div>
          {/if}

        {:else if node.kind === 'strategy'}
          <div class="meta-row">
            <span class="meta-key">family</span>
            <span class="meta-val mono">{node.family || '—'}</span>
          </div>
          <div class="meta-row">
            <span class="meta-key">success</span>
            <span class="meta-val mono">{node.success_count}/{node.attempt_count}</span>
          </div>
          {#if node.template_summary}
            <div class="prose">{node.template_summary}</div>
          {/if}

        {:else if node.kind === 'attempt'}
          <div class="meta-row">
            <span class="meta-key">module</span>
            <span class="meta-val mono">{node.module || '—'}</span>
          </div>
          {#if node.judge_score !== undefined && node.judge_score !== null}
            <div class="meta-row">
              <span class="meta-key">judge</span>
              <span class="meta-val mono">{node.judge_score}</span>
            </div>
          {/if}
          {#if node.experiment_id}
            <div class="meta-row">
              <span class="meta-key">experiment</span>
              <span class="meta-val mono">{node.experiment_id}</span>
            </div>
          {/if}
          {#if node.outcome === 'infrastructure_error' || node.outcome === 'no_observation'}
            <div class="prose dim">
              This attempt is audit-only. It is excluded from belief confidence,
              strategy stats, frontier fulfillment, and ranking history.
            </div>
          {/if}
          {#if (node.tested_hypothesis_ids || []).length}
            <div class="meta-row">
              <span class="meta-key">tested</span>
              <span class="meta-val mono">{node.tested_hypothesis_ids.join(', ')}</span>
            </div>
          {/if}

        {:else if node.kind === 'target'}
          <div class="meta-row">
            <span class="meta-key">hash</span>
            <span class="meta-val mono">{node.target_hash || '—'}</span>
          </div>
          {#if node.traits && Object.keys(node.traits).length}
            <pre class="traits">{JSON.stringify(node.traits, null, 2)}</pre>
          {/if}
        {/if}

      {:else if activeTab === 'raw'}
        <table class="raw-table">
          <tbody>
            {#each rows as [k, v]}
              <tr>
                <td class="rk">{k}</td>
                <td class="rv">{v}</td>
              </tr>
            {/each}
          </tbody>
        </table>

      {:else if activeTab === 'prompt'}
        <pre class="prompt-pre">{promptContext}</pre>
      {/if}
    </div>
  </aside>
{/if}

<style>
  .belief-detail {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--bg-secondary);
    color: var(--text);
    font-size: 12px;
  }

  /* ---------- header ---------- */
  .detail-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 14px 8px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .kind-badge {
    text-transform: uppercase;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    letter-spacing: 0.08em;
    color: var(--kind-color, var(--phosphor));
    border: 1px solid var(--kind-color, var(--phosphor));
    background: color-mix(in srgb, var(--kind-color, var(--phosphor)) 12%, transparent);
    padding: 3px 8px;
    border-radius: 3px;
  }
  .node-id {
    flex: 1;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .close-btn {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 1.4rem;
    cursor: pointer;
    line-height: 1;
    padding: 0 4px;
  }
  .close-btn:hover { color: var(--text); }

  /* ---------- banners (status pill) ---------- */
  .banners {
    padding: 8px 14px 0;
    flex-shrink: 0;
    min-height: 12px;
  }
  .status-badge {
    display: inline-flex;
    align-items: center;
    padding: 4px 11px;
    border-radius: 3px;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    border: 1px solid var(--border);
    background: var(--bg-tertiary);
    color: var(--text-muted);
  }
  /* hypothesis statuses (mirrors NodeDetail's promising/dead/alive) */
  .status-badge.confirmed,
  .status-badge.active {
    background: hsla(155 100% 42% / 0.12);
    border-color: var(--phosphor);
    color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
  }
  .status-badge.refuted {
    background: rgba(239, 68, 68, 0.12);
    border-color: var(--red);
    color: var(--red);
  }
  .status-badge.stale {
    background: transparent;
    border-color: var(--text-muted);
    color: var(--text-muted);
  }
  /* frontier states */
  .status-badge.frontier-proposed,
  .status-badge.frontier-executing {
    background: rgba(245, 158, 11, 0.12);
    border-color: var(--amber);
    color: var(--amber);
  }
  .status-badge.frontier-fulfilled,
  .status-badge.frontier-dropped {
    color: var(--text-muted);
  }
  /* evidence polarities */
  .status-badge.polarity-supports {
    background: hsla(155 100% 42% / 0.12);
    border-color: var(--phosphor);
    color: var(--phosphor);
  }
  .status-badge.polarity-refutes {
    background: rgba(239, 68, 68, 0.12);
    border-color: var(--red);
    color: var(--red);
  }
  /* attempt outcomes */
  .status-badge.attempt-objective_met,
  .status-badge.attempt-leak {
    background: hsla(155 100% 42% / 0.12);
    border-color: var(--phosphor);
    color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
  }
  .status-badge.attempt-dead,
  .status-badge.attempt-refusal {
    background: rgba(239, 68, 68, 0.12);
    border-color: var(--red);
    color: var(--red);
  }
  .status-badge.attempt-infrastructure_error,
  .status-badge.attempt-no_observation {
    background: rgba(245, 158, 11, 0.10);
    border-color: var(--amber);
    color: var(--amber);
  }

  /* ---------- tab strip (mirrors NodeDetail.tab-strip) ---------- */
  .tab-strip {
    flex-shrink: 0;
    display: flex;
    gap: 2px;
    margin: 10px 14px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
    scrollbar-width: none;
  }
  .tab-strip::-webkit-scrollbar { display: none; }
  .tab {
    flex-shrink: 0;
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    padding: 6px 11px;
    cursor: pointer;
    border-radius: 3px 3px 0 0;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    transition: color 120ms, border-color 120ms;
  }
  .tab:hover { color: var(--text); }
  .tab.active {
    color: var(--phosphor);
    border-bottom-color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
  }

  /* ---------- tab content ---------- */
  .tab-content {
    flex: 1;
    overflow-y: auto;
    padding: 12px 14px 24px;
    font-size: 12px;
  }

  /* ---------- summary blocks ---------- */
  .claim {
    font-size: 13px;
    line-height: 1.5;
    color: var(--text);
    margin-bottom: 10px;
    padding-bottom: 8px;
    border-bottom: 1px dashed var(--border);
  }
  .verbatim {
    background: var(--bg-tertiary);
    border-left: 2px solid var(--phosphor);
    padding: 8px 10px;
    border-radius: 0 4px 4px 0;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text);
    margin-bottom: 10px;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.5;
  }
  .prose {
    color: var(--text);
    line-height: 1.55;
    margin: 8px 0;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .prose.dim { color: var(--text-muted); font-size: 11px; }

  .meta-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    padding: 4px 0;
    border-top: 1px dashed var(--border);
  }
  .meta-row:first-of-type { border-top: none; }
  .meta-key {
    color: var(--text-muted);
    font-family: var(--mono);
    font-size: 11px;
    flex-shrink: 0;
  }
  .meta-val {
    color: var(--text);
    text-align: right;
    word-break: break-word;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .meta-val.mono,
  .mono { font-family: var(--mono); font-size: 11px; }

  .component-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 6px;
    margin: 10px 0;
  }
  .component {
    min-width: 0;
    padding: 6px 7px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--bg-tertiary);
  }
  .component-k {
    display: block;
    color: var(--text-muted);
    font-family: var(--mono);
    font-size: 10px;
    line-height: 1.25;
  }
  .component-v {
    display: block;
    margin-top: 3px;
    color: var(--text);
  }

  /* confidence / utility bar */
  .bar {
    width: 100px;
    height: 6px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 3px;
    overflow: hidden;
    display: inline-block;
  }
  .bar-fill {
    display: block;
    height: 100%;
    background: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
  }
  .bar-fill.amber {
    background: var(--amber, #d4a017);
    box-shadow: 0 0 6px hsla(38 92% 50% / 0.55);
  }

  .traits {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    padding: 8px 10px;
    border-radius: 4px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text);
    margin: 8px 0 0;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.5;
  }

  /* ---------- raw fields table ---------- */
  .raw-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  .raw-table td {
    padding: 4px 6px;
    vertical-align: top;
    border-bottom: 1px dashed var(--border);
  }
  .raw-table td.rk {
    color: var(--text-muted);
    width: 38%;
    font-family: var(--mono);
    font-size: 11px;
  }
  .raw-table td.rv {
    color: var(--text);
    word-break: break-word;
    white-space: pre-wrap;
    font-family: var(--mono);
    font-size: 11px;
    line-height: 1.45;
  }

  /* ---------- prompt context ---------- */
  .prompt-pre {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    padding: 10px 12px;
    border-radius: 4px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-muted);
    line-height: 1.55;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
  }
</style>
