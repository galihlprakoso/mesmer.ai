<script>
  import { scenarios, selectedScenario, isRunning, runStatus, graphStats, graphData, modules, visibleStats, modulesDrawerOpen, mode } from '../lib/stores.js'
  import { resetForNewRun } from '../lib/stores.js'

  let maxTurns = null
  let hints = ''
  let fresh = false
  let loading = false

  // Fetch scenarios on mount
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

  // Load saved graph when scenario is selected
  async function loadScenarioGraph(scenarioPath) {
    if (!scenarioPath) {
      $graphData = null
      $graphStats = null
      return
    }
    try {
      // Extract relative path from the scenario list
      const relPath = $scenarios.find(s => s.path === scenarioPath)
      const name = relPath ? scenarioPath : scenarioPath
      const res = await fetch(`/api/scenarios/${encodeURIComponent(name)}`)
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

  // React to scenario selection changes
  $: loadScenarioGraph($selectedScenario)

  import { onMount } from 'svelte'
  onMount(() => {
    loadScenarios()
    loadModules()
  })

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

  async function stopRun() {
    try {
      await fetch('/api/run/stop', { method: 'POST' })
    } catch (e) {
      console.error('Failed to stop run:', e)
    }
  }

</script>

<aside class="sidebar">
  <div class="logo">
    <h1>mesmer</h1>
    <span class="tagline">cognitive hacking toolkit</span>
  </div>

  <section>
    <h3>Scenario</h3>
    <select bind:value={$selectedScenario} disabled={$isRunning}>
      <option value={null}>Select a scenario...</option>
      {#each $scenarios as s}
        <option value={s.path}>{s.name}</option>
      {/each}
    </select>

    {#if $selectedScenario}
      {@const info = $scenarios.find(s => s.path === $selectedScenario)}
      {#if info}
        <div class="scenario-info">
          <span class="badge">{info.target_adapter}</span>
          <span class="module-name">{info.module}</span>
        </div>
      {/if}
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
  </section>

  {#if $visibleStats}
    <section>
      <h3>Graph Stats</h3>
      <div class="stats">
        <div class="stat">
          <span class="stat-value">{$visibleStats.attempts}</span>
          <span class="stat-label">attempts</span>
        </div>
        <div class="stat">
          <span class="stat-value">{$visibleStats.techniques}</span>
          <span class="stat-label">techniques</span>
        </div>
        <div class="stat">
          <span class="stat-value">{$visibleStats.bestScore}</span>
          <span class="stat-label">best score</span>
        </div>
        <div class="stat promising">
          <span class="stat-value">{$visibleStats.promising}</span>
          <span class="stat-label">promising</span>
        </div>
        <div class="stat dead">
          <span class="stat-value">{$visibleStats.dead}</span>
          <span class="stat-label">dead</span>
        </div>
        <div class="stat alive">
          <span class="stat-value">{$visibleStats.alive}</span>
          <span class="stat-label">alive</span>
        </div>
      </div>
    </section>
  {/if}

  <section>
    <h3>Reference</h3>
    <button class="btn btn-ghost" on:click={() => $modulesDrawerOpen = true}>
      📚 Browse modules ({$modules.length})
    </button>
  </section>

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

  .logo h1 {
    font-size: 1.5rem;
    font-weight: 700;
    margin: 0;
    color: var(--accent);
    letter-spacing: -0.02em;
  }

  .tagline {
    font-size: 0.7rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }

  section h3 {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    margin: 0 0 8px 0;
  }

  select {
    width: 100%;
    padding: 8px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-size: 0.85rem;
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
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.7rem;
    font-weight: 600;
  }

  .module-name {
    color: var(--text-muted);
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
    border-radius: 4px;
    color: var(--text);
    width: 100%;
  }

  .btn {
    width: 100%;
    padding: 10px;
    border: none;
    border-radius: 6px;
    font-weight: 600;
    font-size: 0.85rem;
    cursor: pointer;
    transition: background 0.15s;
  }

  .btn-primary {
    background: var(--accent);
    color: #000;
  }
  .btn-primary:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }

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

  .btn-ghost {
    background: transparent;
    color: var(--text-muted);
    border: 1px solid var(--border);
    text-align: left;
    padding: 8px 10px;
    font-size: 0.8rem;
  }
  .btn-ghost:hover { background: var(--bg-tertiary); color: var(--text); }

  .stats {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }

  .stat {
    background: var(--bg-tertiary);
    border-radius: 6px;
    padding: 8px;
    text-align: center;
  }

  .stat-value {
    display: block;
    font-size: 1.2rem;
    font-weight: 700;
    color: var(--text);
  }

  .stat-label {
    font-size: 0.65rem;
    color: var(--text-muted);
    text-transform: uppercase;
  }

  .stat.dead .stat-value { color: var(--red); }
  .stat.promising .stat-value { color: var(--green); }

  .status-bar {
    margin-top: auto;
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.75rem;
    color: var(--text-muted);
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-muted);
  }

  .status-dot.running {
    background: var(--green);
    animation: pulse 1.5s infinite;
  }

  .status-dot.idle { background: var(--text-muted); }
  .status-dot.error { background: var(--red); }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
</style>
