<script>
  import { navigate } from '../lib/router.js'

  /** @type {{path: string, name: string, description?: string, target_adapter: string, module: string, module_tier?: number, has_graph?: boolean, max_turns?: number}} */
  export let scenario

  const TIER_COLORS = ['var(--t0)', 'var(--t1)', 'var(--t2)', 'var(--t3)']

  $: tierColor = TIER_COLORS[scenario.module_tier ?? 2] || 'var(--t-unknown)'

  function openGraph() {
    navigate('graph', scenario.path)
  }

  function openEditor(event) {
    event.stopPropagation()
    navigate('editor', scenario.path)
  }

  function onKey(event) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      openGraph()
    }
  }
</script>

<div
  class="card"
  on:click={openGraph}
  on:keydown={onKey}
  role="button"
  tabindex="0"
  aria-label={`Open ${scenario.name}`}
>
  <div class="card-header">
    <h3 class="card-title">{scenario.name}</h3>
    {#if scenario.has_graph}
      <span class="dot" title="Has prior runs"></span>
    {/if}
  </div>
  {#if scenario.description}
    <p class="card-desc">{scenario.description}</p>
  {/if}
  <div class="card-meta">
    <span class="badge badge-adapter">{scenario.target_adapter}</span>
    <span class="badge badge-module" style="--tier: {tierColor}">
      <span class="tier-dot"></span>
      {(scenario.modules && scenario.modules.length)
        ? scenario.modules.join(' + ')
        : (scenario.module || '—')}
    </span>
    {#if scenario.max_turns}
      <span class="meta-turns">{scenario.max_turns} turns</span>
    {/if}
  </div>
  <div class="card-footer">
    <span class="path">{scenario.path}</span>
    <button
      type="button"
      class="edit-btn"
      on:click={openEditor}
      title="Edit scenario"
      aria-label="Edit scenario"
    >
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path
          d="M11.5 1.5l3 3-9 9-3.5.5.5-3.5 9-9z"
          fill="none"
          stroke="currentColor"
          stroke-width="1.4"
          stroke-linejoin="round"
        />
      </svg>
    </button>
  </div>
</div>

<style>
  .card {
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px;
    text-align: left;
    cursor: pointer;
    color: var(--text);
    display: flex;
    flex-direction: column;
    gap: 10px;
    transition: border-color 0.15s, background 0.15s, transform 0.15s, box-shadow 0.15s;
    min-height: 160px;
  }
  .card:hover {
    border-color: hsla(155 100% 42% / 0.5);
    background: var(--bg-tertiary);
    box-shadow: var(--phosphor-glow-tight);
  }
  .card:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .card-title {
    font-family: var(--font-mono);
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--text);
    margin: 0;
    line-height: 1.3;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
    flex-shrink: 0;
  }

  .card-desc {
    font-size: 0.8rem;
    color: var(--text-muted);
    margin: 0;
    line-height: 1.45;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .card-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    font-size: 0.7rem;
  }

  .badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 3px;
    font-family: var(--font-pixel);
    font-weight: 400;
    font-size: 0.625rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .badge-adapter {
    background: var(--accent-dim);
    color: var(--accent);
    border: 1px solid hsla(155 100% 42% / 0.4);
  }
  .badge-module {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text);
  }
  .tier-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--tier);
    box-shadow: 0 0 4px var(--tier);
  }

  .meta-turns {
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 0.7rem;
    margin-left: auto;
  }

  .card-footer {
    margin-top: auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding-top: 6px;
    border-top: 1px solid var(--border);
  }
  .path {
    font-family: var(--font-mono);
    font-size: 0.65rem;
    color: var(--text-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .edit-btn {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-muted);
    border-radius: 4px;
    width: 28px;
    height: 28px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    flex-shrink: 0;
    transition: color 0.12s, border-color 0.12s, box-shadow 0.12s;
  }
  .edit-btn:hover {
    color: var(--phosphor);
    border-color: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
  }
</style>
