<script>
  import { scenarios, selectedScenario, isRunning, runStatus, graphStats, graphData, modules, modulesDrawerOpen, mode } from '../lib/stores.js'
  import { resetForNewRun } from '../lib/stores.js'
  import { navigate } from '../lib/router.js'

  let maxTurns = null
  let hints = ''
  let fresh = false
  let loading = false
  let testingTarget = false
  let targetTest = null

  // Fetch scenarios on mount — used for the "scenario card meta" lookup
  // (target_adapter/module label below the title) and module count.
  async function loadScenarios() {
    try {
      const res = await fetch('/api/scenarios')
      $scenarios = await res.json()
    } catch (e) {
      console.error('Failed to load scenarios:', e)
    }
  }

  async function loadModules() {
    try {
      const res = await fetch('/api/modules')
      $modules = await res.json()
    } catch (e) {
      console.error('Failed to load modules:', e)
    }
  }

  // Load saved graph when scenario is selected (driven by route → store).
  async function loadScenarioGraph(scenarioPath) {
    if (!scenarioPath) {
      $graphData = null
      $graphStats = null
      return
    }
    try {
      const res = await fetch(`/api/scenarios/${encodeURIComponent(scenarioPath)}`)
      if (res.ok) {
        const data = await res.json()
        if (data.graph) {
          $graphData = data.graph
          $graphStats = data.graph_stats
        } else {
          $graphData = null
          $graphStats = null
        }
      }
    } catch (e) {
      console.error('Failed to load scenario graph:', e)
    }
  }

  $: loadScenarioGraph($selectedScenario)

  import { onMount } from 'svelte'
  onMount(() => {
    loadScenarios()
    loadModules()
  })

  function backToList() {
    navigate('list')
  }

  function editScenario() {
    if ($selectedScenario) navigate('editor', $selectedScenario)
  }

  async function startRun() {
    if (!$selectedScenario) return
    loading = true
    resetForNewRun()
    if (fresh) {
      // User asked to ignore existing graph — clear it locally too; first
      // new snapshot from the server will repopulate.
      $graphData = null
      $graphStats = null
    }

    // Plan mode is for drafting plan.md — clicking Run starts the actual
    // attack using whatever plan.md exists. We fall back to autonomous for
    // the run; the user can switch to co-op before clicking Run if they
    // want the agent to pause and ask questions.
    const runMode = $mode === 'plan' ? 'autonomous' : $mode

    const body = {
      scenario_path: $selectedScenario,
      max_turns: maxTurns || undefined,
      hints: hints.trim() ? hints.trim().split('\n').filter(Boolean) : [],
      fresh,
      mode: runMode,
    }

    try {
      const res = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      if (!res.ok) {
        alert(data.error || 'Failed to start run')
      }
    } catch (e) {
      alert('Failed to start run: ' + e.message)
    } finally {
      loading = false
    }
  }

  async function testTargetConnection() {
    if (!$selectedScenario) return
    testingTarget = true
    targetTest = null
    try {
      const res = await fetch('/api/target/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario_path: $selectedScenario }),
      })
      const data = await res.json()
      targetTest = {
        ok: res.ok && data.ok,
        latency_ms: data.latency_ms,
        response_preview: data.response_preview || '',
        error: data.error || '',
      }
    } catch (e) {
      targetTest = { ok: false, error: e.message }
    } finally {
      testingTarget = false
    }
  }

  async function stopRun() {
    try {
      await fetch('/api/run/stop', { method: 'POST' })
    } catch (e) {
      console.error('Failed to stop run:', e)
    }
  }

</script>

<aside class="sidebar">
  <div class="header">
    <button class="back-link" type="button" on:click={backToList} title="Back to scenarios">
      ← Scenarios
    </button>
    <button
      class="logo-btn"
      type="button"
      on:click={backToList}
      title="Back to scenarios"
      aria-label="Back to scenarios"
    >
      <h1>mesmer</h1>
      <span class="tagline">cognitive hacking toolkit</span>
    </button>
  </div>

  <section>
    <div class="section-head">
      <h3>Scenario</h3>
      <button
        class="edit-pencil"
        type="button"
        on:click={editScenario}
        disabled={!$selectedScenario || $isRunning}
        title="Edit scenario"
        aria-label="Edit scenario"
      >
        <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
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
    {#if $selectedScenario}
      {@const info = $scenarios.find(s => s.path === $selectedScenario)}
      {#if info}
        <div class="scenario-name">{info.name}</div>
        <div class="scenario-info">
          <span class="badge">{info.target_adapter}</span>
          <span class="module-name">{info.module}</span>
        </div>
      {:else}
        <div class="scenario-name">{$selectedScenario}</div>
      {/if}
    {:else}
      <div class="muted">No scenario loaded.</div>
    {/if}
  </section>

  <section>
    <h3>Run Controls</h3>
    <div class="controls">
      <label>
        <span>Max turns</span>
        <input type="number" bind:value={maxTurns} placeholder="default" min="1" max="200" disabled={$isRunning} />
      </label>
      <label class="checkbox">
        <input type="checkbox" bind:checked={fresh} disabled={$isRunning} />
        <span>Fresh (ignore existing graph)</span>
      </label>
    </div>

    {#if $isRunning}
      <button class="btn btn-danger pulsing" on:click={stopRun}>
        <span class="stop-icon">◼</span> Stop Attack
      </button>
    {:else}
      <button class="btn btn-primary" on:click={startRun} disabled={!$selectedScenario || loading}>
        {loading ? 'Starting...' : 'Run Attack'}
      </button>
    {/if}

    <button
      class="btn btn-secondary"
      type="button"
      on:click={testTargetConnection}
      disabled={!$selectedScenario || $isRunning || testingTarget}
    >
      {testingTarget ? 'Testing...' : 'Test Target'}
    </button>

    {#if targetTest}
      <div class:ok={targetTest.ok} class:error={!targetTest.ok} class="target-test">
        <div class="target-test-head">
          <span class="status-dot" class:running={targetTest.ok} class:error={!targetTest.ok}></span>
          <span>{targetTest.ok ? 'Connected' : 'Connection failed'}</span>
          {#if targetTest.latency_ms != null}
            <span class="latency">{targetTest.latency_ms}ms</span>
          {/if}
        </div>
        {#if targetTest.response_preview}
          <div class="target-test-body">{targetTest.response_preview}</div>
        {:else if targetTest.error}
          <div class="target-test-body">{targetTest.error}</div>
        {/if}
      </div>
    {/if}
  </section>

  <button class="modules-link" type="button" on:click={() => $modulesDrawerOpen = true} title="Browse module library">
    <span class="modules-icon">📚</span>
    <span>Modules</span>
    <span class="modules-count">{$modules.length}</span>
  </button>

  <div class="status-bar">
    <span class="status-dot" class:running={$isRunning} class:idle={$runStatus === 'idle'} class:error={$runStatus === 'error'}></span>
    <span>{$runStatus}</span>
  </div>
</aside>

<style>
  .sidebar {
    width: 280px;
    min-width: 280px;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    padding: 16px;
    gap: 16px;
    overflow-y: auto;
  }

  .header {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .back-link {
    background: transparent;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 0.75rem;
    font-weight: 500;
    padding: 0;
    text-align: left;
    align-self: flex-start;
  }
  .back-link:hover { color: var(--text); }

  .logo-btn {
    background: transparent;
    border: none;
    text-align: left;
    cursor: pointer;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .logo-btn h1 {
    font-family: var(--font-pixel);
    font-size: 1.4rem;
    font-weight: 400;
    margin: 0;
    color: var(--phosphor);
    letter-spacing: 0.04em;
    text-shadow: var(--phosphor-glow);
    text-transform: lowercase;
  }

  .tagline {
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }

  section h3 {
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    margin: 0;
    font-weight: 400;
  }

  .section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }

  .edit-pencil {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-muted);
    border-radius: 4px;
    padding: 4px 6px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    transition: color 0.12s, border-color 0.12s;
  }
  .edit-pencil:hover:not(:disabled) {
    color: var(--accent);
    border-color: var(--accent);
  }
  .edit-pencil:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .scenario-name {
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 4px;
  }
  .muted {
    font-size: 0.8rem;
    color: var(--text-muted);
  }

  .scenario-info {
    margin-top: 6px;
    display: flex;
    gap: 6px;
    align-items: center;
    font-size: 0.75rem;
  }

  .badge {
    background: var(--accent-dim);
    color: var(--accent);
    border: 1px solid hsla(155 100% 42% / 0.4);
    padding: 2px 6px;
    border-radius: 3px;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .module-name {
    color: var(--text-muted);
    font-family: var(--font-mono);
  }

  .controls {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 10px;
  }

  .controls label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 0.8rem;
    color: var(--text-muted);
  }

  .controls label.checkbox {
    flex-direction: row;
    align-items: center;
    gap: 6px;
  }

  .controls input[type="number"] {
    padding: 6px 8px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text);
    font-family: var(--font-mono);
    width: 100%;
  }
  .controls input[type="number"]:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: var(--phosphor-glow-tight);
  }

  .btn {
    width: 100%;
    padding: 10px;
    border: none;
    border-radius: 4px;
    font-family: var(--font-pixel);
    font-weight: 400;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    cursor: pointer;
    transition: background 0.15s, box-shadow 0.15s, filter 0.15s;
  }

  .btn-primary {
    background: var(--accent);
    color: #000;
    box-shadow: var(--button-glow);
  }
  .btn-primary:hover:not(:disabled) { filter: brightness(1.1); }
  .btn-primary:disabled { opacity: 0.4; cursor: not-allowed; box-shadow: none; }

  .btn-secondary {
    margin-top: 8px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text);
    box-shadow: none;
  }
  .btn-secondary:hover:not(:disabled) {
    border-color: hsla(155 100% 42% / 0.5);
    color: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
  }
  .btn-secondary:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .btn-danger {
    background: var(--red);
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }
  .btn-danger:hover { background: #dc2626; }
  .stop-icon {
    font-size: 0.7rem;
    display: inline-block;
    line-height: 1;
  }

  .btn.pulsing {
    animation: btnPulse 1.6s infinite;
  }
  @keyframes btnPulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.55); }
    50%      { box-shadow: 0 0 0 10px rgba(239, 68, 68, 0); }
  }

  .modules-link {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 10px;
    align-self: flex-start;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: color 120ms, border-color 120ms, box-shadow 120ms;
  }
  .modules-link:hover {
    color: var(--phosphor);
    border-color: hsla(155 100% 42% / 0.5);
    box-shadow: var(--phosphor-glow-tight);
  }
  .modules-icon { font-size: 0.85rem; line-height: 1; }
  .modules-count {
    padding: 1px 6px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 3px;
    font-family: var(--font-mono);
    font-size: 0.65rem;
    color: var(--text-muted);
  }

  .target-test {
    margin-top: 8px;
    padding: 8px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--bg-tertiary);
    font-size: 0.72rem;
  }
  .target-test.ok {
    border-color: hsla(155 100% 42% / 0.35);
  }
  .target-test.error {
    border-color: hsla(0 84% 60% / 0.45);
  }
  .target-test-head {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--text);
    font-family: var(--font-pixel);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .latency {
    margin-left: auto;
    color: var(--text-muted);
    font-family: var(--font-mono);
    letter-spacing: 0;
    text-transform: none;
  }
  .target-test-body {
    margin-top: 6px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    line-height: 1.35;
    max-height: 72px;
    overflow: hidden;
    overflow-wrap: anywhere;
  }

  .status-bar {
    margin-top: auto;
    display: flex;
    align-items: center;
    gap: 6px;
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-muted);
  }

  .status-dot.running {
    background: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
    animation: pulse 1.5s infinite;
  }

  .status-dot.idle { background: var(--text-muted); }
  .status-dot.error { background: var(--red); }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
</style>
