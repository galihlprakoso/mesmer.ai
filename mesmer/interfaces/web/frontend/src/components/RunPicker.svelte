<script>
  import {
    runs, selectedRunId, runStatus,
    activeModuleTop, selectedNode,
  } from '../lib/stores.js'

  // The "live" run is always the newest one by sequence — when the
  // backend is mid-run there's no verdict yet, but the run shows up in
  // `runs` as soon as the first node lands. Cheaper than threading a
  // dedicated runId through the WS bus.
  $: latest = $runs.length > 0 ? $runs[$runs.length - 1] : null
  $: isLiveRun = $runStatus === 'running' && latest !== null

  // Snap to latest on first load AND whenever a NEW run arrives (its
  // runId hasn't been seen before). Clicking an older chip stays sticky
  // for as long as the latest hasn't changed — the moment a new run
  // starts, selection follows it (matches "Run Attack focuses the new
  // run" without needing a hook in the sidebar).
  let lastSeenLatest = null
  $: if ($runs.length > 0) {
    const newLatest = $runs[$runs.length - 1].runId
    if ($selectedRunId === null || newLatest !== lastSeenLatest) {
      selectedRunId.set(newLatest)
    }
    lastSeenLatest = newLatest
  }

  function pick(runId) {
    selectedRunId.set(runId)
    selectedNode.set(null)  // clear stale detail panel from a different run
  }

  function shortId(rid) {
    return (rid || '').slice(0, 8)
  }

  function fmtTimestamp(ts) {
    if (!ts) return ''
    try { return new Date(ts * 1000).toLocaleString() } catch { return '' }
  }

  function chipTitle(r) {
    const verdictText =
      r.verdict === 'met' ? 'Objective met'
      : r.verdict === 'not_met' ? 'No consolidation'
      : (isLiveRun && r === latest) ? 'Currently running'
      : 'Pending / interrupted'
    let title = `Run #${r.seq}\n${shortId(r.runId)}\n${fmtTimestamp(r.timestamp)}\n${verdictText}`
    if (r === latest && !(isLiveRun && r === latest)) {
      title += '\n(live state — next run starts from here)'
    }
    return title
  }

  function chipIcon(r) {
    if (isLiveRun && r === latest) return '●'  // ●
    if (r.verdict === 'met') return '✓'  // ✓
    if (r.verdict === 'not_met') return '✗'  // ✗
    return '○'  // ○
  }

  async function copyRunId(event, runId) {
    event.stopPropagation()
    try {
      await navigator.clipboard.writeText(runId || '')
    } catch {
      // Clipboard can be unavailable in non-secure browser contexts.
    }
  }
</script>

{#if $runs.length > 0}
  <div class="run-picker" role="tablist" aria-label="Run picker">
    {#each $runs as r (r.runId)}
      <button
        class="chip {r.verdict}"
        class:active={$selectedRunId === r.runId}
        class:live={isLiveRun && r === latest}
        class:latest={r === latest && !(isLiveRun && r === latest)}
        role="tab"
        aria-selected={$selectedRunId === r.runId}
        title={chipTitle(r)}
        on:click={() => pick(r.runId)}
      >
        <span class="ico">{chipIcon(r)}</span>
        <span class="seq">#{r.seq}</span>
      </button>
      <button
        class="copy-run"
        aria-label="Copy run ID for run #{r.seq}"
        title={`Copy run ID\n${r.runId}`}
        on:click={(event) => copyRunId(event, r.runId)}
      >
        ID
      </button>
    {/each}
    {#if isLiveRun && $activeModuleTop}
      <span class="active-mod" title="Currently inside: {$activeModuleTop}">
        <span class="dot"></span>
        {$activeModuleTop}
      </span>
    {/if}
  </div>
{/if}

<style>
  .run-picker {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 6px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 4px;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    overflow-x: auto;
    max-width: min(50vw, 600px);
    scrollbar-width: thin;
  }
  .run-picker::-webkit-scrollbar { height: 4px; }
  .run-picker::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

  .chip {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 9px;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 400;
    cursor: pointer;
    transition: color 120ms, border-color 120ms, background 120ms, box-shadow 120ms;
  }
  .chip:hover { color: var(--text); border-color: var(--text-muted); }
  .chip.active {
    background: var(--accent);
    color: #000;
    border-color: var(--accent);
  }
  .chip .ico { font-size: 0.78rem; line-height: 1; }
  .chip .seq { font-family: inherit; }

  /* Verdict-tinted icons (only when chip is NOT in active state — active
     uses the accent fg). */
  .chip.met:not(.active) .ico { color: var(--green); }
  .chip.not_met:not(.active) .ico { color: var(--red); }
  .chip.pending:not(.active) .ico { color: var(--text-muted); }

  /* Live (currently-running) chip pulses regardless of selection. */
  .chip.live:not(.active) {
    border-color: var(--phosphor);
    color: var(--phosphor);
  }
  .chip.live .ico { color: var(--phosphor); }
  .chip.live {
    animation: chipPulse 1.4s infinite;
  }
  @keyframes chipPulse {
    0%   { box-shadow: 0 0 0 0 hsla(155, 100%, 42%, 0.55); }
    70%  { box-shadow: 0 0 0 7px hsla(155, 100%, 42%, 0); }
    100% { box-shadow: 0 0 0 0 hsla(155, 100%, 42%, 0); }
  }

  /* "Latest finished run" — small green dot anchored top-right of the chip,
     marks "this is the state next Run Attack inherits from". */
  .chip.latest {
    position: relative;
    border-color: var(--green);
  }
  .chip.latest::after {
    content: '';
    position: absolute;
    top: -2px;
    right: -2px;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--green);
    border: 1px solid var(--bg-secondary);
  }
  .chip.latest.active::after { display: none; }

  .copy-run {
    flex-shrink: 0;
    height: 20px;
    padding: 0 5px;
    margin-left: -2px;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.55rem;
    letter-spacing: 0.08em;
    cursor: pointer;
  }
  .copy-run:hover {
    color: var(--phosphor);
    border-color: var(--phosphor);
  }

  .active-mod {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 0 8px 0 6px;
    color: var(--phosphor);
    font-family: var(--mono);
    font-size: 0.65rem;
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .active-mod .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--phosphor);
    animation: chipPulse 1.4s infinite;
  }
</style>
