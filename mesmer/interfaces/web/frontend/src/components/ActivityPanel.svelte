<script>
  import { derived } from 'svelte/store'
  import { afterUpdate } from 'svelte'
  import {
    events, activeModules, activeModuleTop, isRunning,
    visibleStats, runMeta, keyStatus,
  } from '../lib/stores.js'
  import { eventsToActivity } from '../lib/activity-transform.js'
  import ActivityRow from './ActivityRow.svelte'

  const rows = derived(events, $events => eventsToActivity($events))

  let scrollEl
  let autoScroll = true

  function onScroll() {
    if (!scrollEl) return
    const nearBottom = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight < 40
    autoScroll = nearBottom
  }

  afterUpdate(() => {
    if (autoScroll && scrollEl) {
      scrollEl.scrollTop = scrollEl.scrollHeight
    }
  })
</script>

<aside class="activity-panel">
  <div class="header">
    <div class="header-top">
      <div class="live-indicator">
        <span class="live-dot"></span>
        <span class="live-label">LIVE</span>
      </div>
      {#if $keyStatus && $keyStatus.total > 0}
        <span
          class="keys-pill"
          class:exhausted={$keyStatus.active === 0}
          class:degraded={$keyStatus.active > 0 && $keyStatus.active < $keyStatus.total}
          title={(($keyStatus.keys || []).map(k => k.masked + (k.cooled_until ? ` (cooled, ${k.reason || '?'})` : ' (active)')).join('\n'))}
        >
          keys: {$keyStatus.active}/{$keyStatus.total} active
        </span>
      {/if}
    </div>
    <div class="current-module">
      {#if $activeModuleTop}
        <span class="module-name">{$activeModuleTop}</span>
        {#if $activeModules.length > 1}
          <span class="stack-depth">depth {$activeModules.length}</span>
        {/if}
      {:else}
        <span class="module-name idle">idle</span>
      {/if}
    </div>
  </div>

  {#if $visibleStats}
    <div class="stats-strip">
      <div class="stat">
        <span class="stat-label">Attempts</span>
        <span class="stat-value">{$visibleStats.attempts}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Best</span>
        <span class="stat-value best">{$visibleStats.bestScore}/10</span>
      </div>
      <div class="stat">
        <span class="stat-label">Promising</span>
        <span class="stat-value promising">{$visibleStats.promising}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Dead</span>
        <span class="stat-value dead">{$visibleStats.dead}</span>
      </div>
    </div>
  {/if}

  <div class="feed" bind:this={scrollEl} on:scroll={onScroll}>
    {#if $rows.length === 0}
      <div class="empty">
        <span class="spinner"></span>
        <span>Waiting for activity…</span>
      </div>
    {:else}
      {#each $rows as row, i (i + (row.time || 0))}
        <ActivityRow entry={row} />
      {/each}
    {/if}
  </div>

  {#if !autoScroll}
    <button class="jump-btn" on:click={() => { autoScroll = true; if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight }}>
      ↓ Jump to latest
    </button>
  {/if}
</aside>

<style>
  .activity-panel {
    width: 340px;
    min-width: 340px;
    background: var(--bg-secondary);
    border-left: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    animation: slideIn 0.18s ease-out;
    position: relative;
  }

  @keyframes slideIn {
    from { transform: translateX(20px); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }

  .header {
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-primary);
  }

  .header-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    margin-bottom: 4px;
  }

  .live-indicator {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .keys-pill {
    font-size: 0.62rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    padding: 1px 7px;
    border-radius: 10px;
    background: var(--bg-tertiary);
    color: var(--text-muted);
    border: 1px solid var(--border);
    font-family: 'JetBrains Mono', monospace;
    cursor: help;
  }
  .keys-pill.degraded {
    color: #f59e0b;
    border-color: #f59e0b55;
    background: #f59e0b11;
  }
  .keys-pill.exhausted {
    color: var(--red);
    border-color: #ef444466;
    background: #ef444411;
  }

  .live-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #06b6d4;
    animation: livePulse 1.3s infinite;
  }

  @keyframes livePulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(6, 182, 212, 0.7); }
    70%      { box-shadow: 0 0 0 6px rgba(6, 182, 212, 0); }
  }

  .live-label {
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: #06b6d4;
  }

  .current-module {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }

  .module-name {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--text);
    word-break: break-word;
  }

  .module-name.idle {
    color: var(--text-muted);
    font-style: italic;
  }

  .stack-depth {
    font-size: 0.65rem;
    color: var(--text-muted);
    font-family: monospace;
  }

  .stats-strip {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 4px;
    padding: 8px 10px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-secondary);
  }

  .stat {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
  }

  .stat-label {
    font-size: 0.58rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
  }

  .stat-value {
    font-size: 0.95rem;
    font-weight: 700;
    color: var(--text);
    font-family: monospace;
  }

  .stat-value.best      { color: var(--green); }
  .stat-value.promising { color: var(--green); }
  .stat-value.dead      { color: var(--red); }

  .feed {
    flex: 1;
    overflow-y: auto;
  }

  .empty {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 30px 20px;
    color: var(--text-muted);
    font-size: 0.8rem;
    font-style: italic;
  }

  .spinner {
    width: 12px; height: 12px;
    border: 2px solid var(--border);
    border-top-color: #06b6d4;
    border-radius: 50%;
    animation: spin 0.9s linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .jump-btn {
    position: absolute;
    bottom: 14px;
    right: 14px;
    padding: 5px 10px;
    background: var(--accent);
    color: #000;
    border: none;
    border-radius: 20px;
    font-size: 0.7rem;
    font-weight: 600;
    cursor: pointer;
    box-shadow: 0 4px 12px rgba(0,0,0,0.35);
  }
  .jump-btn:hover { background: var(--accent-hover); }
</style>
