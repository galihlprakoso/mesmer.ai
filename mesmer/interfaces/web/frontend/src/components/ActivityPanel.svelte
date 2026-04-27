<script>
  import { derived } from 'svelte/store'
  import { afterUpdate } from 'svelte'
  import {
    events, activeModules, activeModuleTop, isRunning, runStatus,
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
      <div class="live-indicator" class:running={$isRunning} class:error={$runStatus === 'error'}>
        <span class="live-dot"></span>
        <span class="live-label">{$isRunning ? 'LIVE' : ($runStatus === 'error' ? 'ERROR' : $runStatus.toUpperCase() || 'IDLE')}</span>
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
        <span class="stat-label">Completed</span>
        <span class="stat-value promising">{$visibleStats.completed}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Failed</span>
        <span class="stat-value dead">{$visibleStats.failed}</span>
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
    flex: 1;
    min-height: 0;
    background: var(--bg-secondary);
    display: flex;
    flex-direction: column;
    position: relative;
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
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 1px 7px;
    border-radius: 3px;
    background: var(--bg-tertiary);
    color: var(--text-muted);
    border: 1px solid var(--border);
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
    background: var(--text-muted);
  }
  .live-indicator.running .live-dot {
    background: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
    animation: livePulse 1.3s infinite;
  }
  .live-indicator.error .live-dot {
    background: var(--red);
  }

  @keyframes livePulse {
    0%, 100% { box-shadow: 0 0 0 0 hsla(155, 100%, 42%, 0.7); }
    70%      { box-shadow: 0 0 0 6px hsla(155, 100%, 42%, 0); }
  }

  .live-label {
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    font-weight: 400;
    letter-spacing: 0.1em;
    color: var(--text-muted);
  }
  .live-indicator.running .live-label { color: var(--phosphor); text-shadow: var(--phosphor-glow); }
  .live-indicator.error   .live-label { color: var(--red); }

  .current-module {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }

  .module-name {
    font-family: var(--font-mono);
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
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
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
    font-family: var(--font-pixel);
    font-size: 0.5625rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
  }

  .stat-value {
    font-family: var(--font-mono);
    font-size: 0.95rem;
    font-weight: 700;
    color: var(--text);
  }

  .stat-value.best      { color: var(--phosphor); text-shadow: var(--phosphor-glow); }
  .stat-value.promising { color: var(--phosphor); }
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
    border-top-color: var(--phosphor);
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
    border-radius: 4px;
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
    box-shadow: var(--button-glow);
  }
  .jump-btn:hover { filter: brightness(1.1); }
</style>
