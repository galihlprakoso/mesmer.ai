<script>
  import { onMount, onDestroy } from 'svelte'
  import {
    runs, selectedRunId, runStatus,
    activeModuleTop, selectedNode,
  } from '../lib/stores.js'

  const FILTERS = [
    { key: 'all', label: 'All' },
    { key: 'live', label: 'Live' },
    { key: 'met', label: 'Met' },
    { key: 'not_met', label: 'Failed' },
    { key: 'pending', label: 'Pending' },
  ]

  let pickerEl
  let menuOpen = false
  let query = ''
  let filter = 'all'

  // The "live" run is always the newest one by sequence. When the
  // backend is mid-run there is no verdict yet, but the run shows up in
  // `runs` as soon as the first node lands.
  $: latest = $runs.length > 0 ? $runs[$runs.length - 1] : null
  $: isLiveRun = $runStatus === 'running' && latest !== null

  // Snap to latest on first load and when a new run appears. Manual
  // selection stays sticky until the next run arrives.
  let lastSeenLatest = null
  $: if ($runs.length > 0) {
    const newLatest = $runs[$runs.length - 1].runId
    const selectedStillExists = $runs.some(r => r.runId === $selectedRunId)
    if ($selectedRunId === null || !selectedStillExists || newLatest !== lastSeenLatest) {
      selectedRunId.set(newLatest)
    }
    lastSeenLatest = newLatest
  }

  $: selectedRun = $runs.find(r => r.runId === $selectedRunId) || latest
  $: selectedIndex = selectedRun
    ? $runs.findIndex(r => r.runId === selectedRun.runId)
    : -1
  $: previousRun = selectedIndex > 0 ? $runs[selectedIndex - 1] : null
  $: nextRun = selectedIndex >= 0 && selectedIndex < $runs.length - 1
    ? $runs[selectedIndex + 1]
    : null
  $: normalizedQuery = query.trim().toLowerCase()
  $: filteredRuns = $runs.filter(r => matchesFilter(r, normalizedQuery, filter))
  $: menuRuns = [...filteredRuns].reverse()

  onMount(() => {
    document.addEventListener('pointerdown', handleDocumentPointerDown)
  })

  onDestroy(() => {
    document.removeEventListener('pointerdown', handleDocumentPointerDown)
  })

  function isLatest(r) {
    return latest !== null && r?.runId === latest.runId
  }

  function isLive(r) {
    return isLiveRun && isLatest(r)
  }

  function pick(runId, close = true) {
    selectedRunId.set(runId)
    selectedNode.set(null)
    if (close) menuOpen = false
  }

  function pickRelative(delta) {
    const target = delta < 0 ? previousRun : nextRun
    if (target) pick(target.runId, false)
  }

  function shortId(rid) {
    return (rid || '').slice(0, 8)
  }

  function fmtTimestamp(ts) {
    if (!ts) return ''
    try { return new Date(ts * 1000).toLocaleString() } catch { return '' }
  }

  function fmtShortTimestamp(ts) {
    if (!ts) return ''
    try {
      const date = new Date(ts * 1000)
      const today = new Date()
      const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      if (date.toDateString() === today.toDateString()) return time
      return `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`
    } catch {
      return ''
    }
  }

  function statusClass(r) {
    if (!r) return 'pending'
    if (isLive(r)) return 'live'
    return r.verdict || 'pending'
  }

  function statusLabel(r) {
    if (!r) return 'Pending'
    if (isLive(r)) return 'Live'
    if (r.verdict === 'met') return 'Objective met'
    if (r.verdict === 'not_met') return 'Failed'
    return 'Pending'
  }

  function runTitle(r) {
    if (!r) return ''
    let title = `Run #${r.seq}\n${shortId(r.runId)}\n${fmtTimestamp(r.timestamp)}\n${statusLabel(r)}`
    if (isLatest(r) && !isLive(r)) {
      title += '\nLatest finished run'
    }
    return title
  }

  function matchesFilter(r, search, mode) {
    if (mode === 'live' && !isLive(r)) return false
    if (mode !== 'all' && mode !== 'live' && r.verdict !== mode) return false
    if (!search) return true

    const haystack = [
      r.seq,
      `#${r.seq}`,
      r.runId,
      shortId(r.runId),
      statusLabel(r),
      fmtTimestamp(r.timestamp),
    ].join(' ').toLowerCase()
    return haystack.includes(search)
  }

  async function copyRunId(event, runId = selectedRun?.runId) {
    event?.stopPropagation()
    try {
      await navigator.clipboard.writeText(runId || '')
    } catch {
      // Clipboard can be unavailable in non-secure browser contexts.
    }
  }

  function handleDocumentPointerDown(event) {
    if (pickerEl && !pickerEl.contains(event.target)) {
      menuOpen = false
    }
  }

  function handlePickerKeydown(event) {
    if (event.key === 'Escape') {
      menuOpen = false
      return
    }
    if (event.target?.tagName === 'INPUT') return
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      pickRelative(-1)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      pickRelative(1)
    }
  }
</script>

{#if $runs.length > 0 && selectedRun}
  <div
    class="run-picker-shell"
    class:open={menuOpen}
    bind:this={pickerEl}
    role="group"
    aria-label="Run switcher"
  >
    <div
      class="run-picker"
      role="toolbar"
      aria-label="Run navigation"
      tabindex="-1"
      on:keydown={handlePickerKeydown}
    >
      <button
        type="button"
        class="nav-btn"
        disabled={!previousRun}
        aria-label="Previous run"
        title={previousRun ? `Previous: run #${previousRun.seq}` : 'No previous run'}
        on:click={() => pickRelative(-1)}
      >
        <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <polyline points="10,3 5,8 10,13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>

      <button
        type="button"
        class="current-run"
        aria-haspopup="listbox"
        aria-expanded={menuOpen}
        title={runTitle(selectedRun)}
        on:click={() => menuOpen = !menuOpen}
      >
        <span class="state-dot {statusClass(selectedRun)}" aria-hidden="true"></span>
        <span class="current-copy">
          <span class="current-title">Run #{selectedRun.seq} of {$runs.length}</span>
          <span class="current-meta">
            {statusLabel(selectedRun)} / {shortId(selectedRun.runId)}
          </span>
        </span>
        <svg class="caret" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <polyline points="4,6 8,10 12,6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>

      <button
        type="button"
        class="nav-btn"
        disabled={!nextRun}
        aria-label="Next run"
        title={nextRun ? `Next: run #${nextRun.seq}` : 'No next run'}
        on:click={() => pickRelative(1)}
      >
        <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <polyline points="6,3 11,8 6,13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>

      <button
        type="button"
        class="copy-run"
        aria-label="Copy selected run ID"
        title={`Copy run ID\n${selectedRun.runId}`}
        on:click={(event) => copyRunId(event)}
      >
        ID
      </button>
    </div>

    {#if isLiveRun && $activeModuleTop}
      <div class="active-mod" title="Currently inside: {$activeModuleTop}">
        <span class="state-dot live" aria-hidden="true"></span>
        <span>{$activeModuleTop}</span>
      </div>
    {/if}

    {#if menuOpen}
      <div class="run-menu">
        <div class="menu-head">
          <span class="menu-title">Runs {$runs.length}</span>
          <button
            type="button"
            class="latest-btn"
            disabled={!latest || selectedRun.runId === latest.runId}
            on:click={() => latest && pick(latest.runId)}
          >
            Latest
          </button>
        </div>

        <input
          class="run-search"
          bind:value={query}
          placeholder="Filter by #, ID, status"
          aria-label="Filter runs"
        />

        <div class="filter-tabs" role="tablist" aria-label="Run status filters">
          {#each FILTERS as option}
            <button
              type="button"
              role="tab"
              class:active={filter === option.key}
              aria-selected={filter === option.key}
              on:click={() => filter = option.key}
            >
              {option.label}
            </button>
          {/each}
        </div>

        <div class="run-list" role="listbox" aria-label="Runs">
          {#if menuRuns.length === 0}
            <div class="empty-runs">No matching runs</div>
          {:else}
            {#each menuRuns as r (r.runId)}
              <button
                type="button"
                class="run-row"
                class:selected={selectedRun.runId === r.runId}
                role="option"
                aria-selected={selectedRun.runId === r.runId}
                title={runTitle(r)}
                on:click={() => pick(r.runId)}
              >
                <span class="row-status">
                  <span class="state-dot {statusClass(r)}" aria-hidden="true"></span>
                  <span>{statusLabel(r)}</span>
                </span>
                <span class="row-main">
                  <span class="row-title">
                    Run #{r.seq}
                    {#if isLatest(r)}
                      <span class="latest-mark">latest</span>
                    {/if}
                  </span>
                  <span class="row-id">{shortId(r.runId)}</span>
                </span>
                <span class="row-time">{fmtShortTimestamp(r.timestamp)}</span>
              </button>
            {/each}
          {/if}
        </div>
      </div>
    {/if}
  </div>
{/if}

<style>
  .run-picker-shell {
    position: relative;
    width: min(42vw, 460px);
    min-width: 332px;
    font-family: var(--font-mono);
    font-size: 0.7rem;
  }

  .run-picker {
    display: flex;
    align-items: stretch;
    gap: 4px;
    padding: 4px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 4px;
    box-shadow: 0 8px 24px hsla(0 0% 0% / 0.18);
  }

  .nav-btn,
  .copy-run,
  .current-run,
  .latest-btn,
  .filter-tabs button,
  .run-row {
    font-family: var(--font-pixel);
    letter-spacing: 0.08em;
    cursor: pointer;
  }

  .nav-btn,
  .copy-run {
    flex: 0 0 28px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-muted);
    transition: color 120ms, border-color 120ms, background 120ms, box-shadow 120ms;
  }

  .nav-btn svg {
    width: 15px;
    height: 15px;
  }

  .nav-btn:hover:not(:disabled),
  .copy-run:hover {
    color: var(--phosphor);
    border-color: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
  }

  .nav-btn:disabled {
    opacity: 0.35;
    cursor: not-allowed;
  }

  .copy-run {
    flex-basis: 34px;
    font-size: 0.55rem;
    text-transform: uppercase;
  }

  .current-run {
    min-width: 0;
    flex: 1;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 8px;
    padding: 5px 8px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text);
    text-align: left;
    transition: color 120ms, border-color 120ms, background 120ms;
  }

  .current-run:hover,
  .run-picker-shell.open .current-run {
    border-color: var(--phosphor);
    color: var(--phosphor);
  }

  .current-copy {
    display: grid;
    min-width: 0;
    gap: 1px;
  }

  .current-title,
  .current-meta,
  .row-title,
  .row-id,
  .row-time,
  .active-mod span:last-child {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .current-title {
    color: inherit;
    font-size: 0.68rem;
    text-transform: uppercase;
  }

  .current-meta {
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 0.66rem;
    letter-spacing: 0;
    text-transform: none;
  }

  .caret {
    width: 14px;
    height: 14px;
    color: var(--text-muted);
    transition: transform 120ms;
  }

  .run-picker-shell.open .caret {
    transform: rotate(180deg);
  }

  .state-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    display: inline-block;
    flex: 0 0 auto;
    background: var(--text-muted);
  }

  .state-dot.live {
    background: var(--amber);
    animation: dotPulse 1.4s infinite;
  }

  .state-dot.met {
    background: var(--green);
    box-shadow: var(--phosphor-glow-tight);
  }

  .state-dot.not_met {
    background: var(--red);
  }

  .state-dot.pending {
    background: var(--text-muted);
  }

  @keyframes dotPulse {
    0%   { box-shadow: 0 0 0 0 hsla(38, 92%, 50%, 0.55); }
    70%  { box-shadow: 0 0 0 7px hsla(38, 92%, 50%, 0); }
    100% { box-shadow: 0 0 0 0 hsla(38, 92%, 50%, 0); }
  }

  .active-mod {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    gap: 6px;
    max-width: 100%;
    margin-top: 4px;
    padding: 4px 8px;
    background: color-mix(in srgb, var(--bg-secondary) 88%, transparent);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--phosphor);
    font-family: var(--font-mono);
    font-size: 0.65rem;
    font-weight: 600;
    letter-spacing: 0.04em;
  }

  .run-menu {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    z-index: 20;
    width: 100%;
    display: grid;
    gap: 8px;
    padding: 8px;
    background: var(--bg-secondary);
    border: 1px solid var(--border-strong);
    border-radius: 5px;
    box-shadow: 0 20px 48px hsla(0 0% 0% / 0.42);
  }

  .menu-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .menu-title {
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.63rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .latest-btn {
    padding: 3px 8px;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-muted);
    font-size: 0.58rem;
    text-transform: uppercase;
  }

  .latest-btn:hover:not(:disabled) {
    color: var(--phosphor);
    border-color: var(--phosphor);
  }

  .latest-btn:disabled {
    opacity: 0.35;
    cursor: not-allowed;
  }

  .run-search {
    width: 100%;
    padding: 7px 8px;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 0.74rem;
    outline: none;
  }

  .run-search:focus {
    border-color: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
  }

  .filter-tabs {
    display: flex;
    gap: 3px;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .filter-tabs::-webkit-scrollbar {
    display: none;
  }

  .filter-tabs button {
    flex: 0 0 auto;
    padding: 4px 7px;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-muted);
    font-size: 0.55rem;
    text-transform: uppercase;
  }

  .filter-tabs button:hover {
    color: var(--text);
    border-color: var(--border-strong);
  }

  .filter-tabs button.active {
    color: #000;
    background: var(--accent);
    border-color: var(--accent);
  }

  .run-list {
    max-height: min(48vh, 360px);
    overflow-y: auto;
    display: grid;
    gap: 4px;
    padding-right: 2px;
  }

  .run-row {
    width: 100%;
    display: grid;
    grid-template-columns: minmax(90px, 0.9fr) minmax(0, 1.2fr) minmax(58px, auto);
    align-items: center;
    gap: 8px;
    padding: 7px 8px;
    background: var(--bg-tertiary);
    border: 1px solid transparent;
    border-radius: 3px;
    color: var(--text);
    text-align: left;
  }

  .run-row:hover {
    border-color: var(--border-strong);
  }

  .run-row.selected {
    border-color: var(--phosphor);
    background: var(--accent-dim);
  }

  .row-status {
    min-width: 0;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--text-muted);
    font-size: 0.56rem;
    text-transform: uppercase;
  }

  .row-main {
    min-width: 0;
    display: grid;
    gap: 1px;
  }

  .row-title {
    font-size: 0.64rem;
    text-transform: uppercase;
  }

  .row-id,
  .row-time {
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 0.64rem;
    letter-spacing: 0;
    text-transform: none;
  }

  .row-time {
    text-align: right;
  }

  .latest-mark {
    margin-left: 5px;
    color: var(--phosphor);
    font-size: 0.52rem;
  }

  .empty-runs {
    padding: 18px 8px;
    color: var(--text-muted);
    text-align: center;
    font-family: var(--font-pixel);
    font-size: 0.62rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  @media (max-width: 760px) {
    .run-picker-shell {
      width: calc(100vw - 24px);
      min-width: 0;
    }

    .run-row {
      grid-template-columns: minmax(76px, 0.8fr) minmax(0, 1fr);
    }

    .row-time {
      display: none;
    }
  }
</style>
