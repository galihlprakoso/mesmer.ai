<script>
  import { onMount } from 'svelte'
  import { listScenarios } from '../lib/api.js'
  import { navigate } from '../lib/router.js'
  import ScenarioCard from '../components/ScenarioCard.svelte'

  let scenarios = []
  let loading = true
  let error = null

  async function load() {
    loading = true
    error = null
    try {
      scenarios = await listScenarios()
    } catch (e) {
      error = e.message || 'Failed to load scenarios'
    } finally {
      loading = false
    }
  }

  onMount(load)

  function createNew() {
    navigate('editor', null)
  }
</script>

<div class="page">
  <header class="page-header">
    <div class="logo">
      <h1>mesmer</h1>
      <span class="tagline">cognitive hacking toolkit</span>
    </div>
    <button class="btn btn-primary" type="button" on:click={createNew}>
      <span class="plus">+</span> New scenario
    </button>
  </header>

  <main class="page-body">
    <div class="title-row">
      <h2>Scenarios</h2>
      {#if !loading && !error}
        <span class="count">{scenarios.length} total</span>
      {/if}
    </div>

    {#if loading}
      <div class="empty">Loading scenarios…</div>
    {:else if error}
      <div class="empty error">
        <p>{error}</p>
        <button class="btn btn-ghost" type="button" on:click={load}>Retry</button>
      </div>
    {:else if scenarios.length === 0}
      <div class="empty">
        <p>No scenarios yet.</p>
        <button class="btn btn-primary" type="button" on:click={createNew}>
          Create your first scenario
        </button>
      </div>
    {:else}
      <div class="grid">
        {#each scenarios as s (s.path)}
          <ScenarioCard scenario={s} />
        {/each}
      </div>
    {/if}
  </main>
</div>

<style>
  .page {
    height: 100vh;
    width: 100vw;
    display: flex;
    flex-direction: column;
    background: var(--bg-primary);
    overflow: hidden;
  }

  .page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 18px 28px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-secondary);
    flex-shrink: 0;
  }

  .logo {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .logo h1 {
    font-family: var(--font-pixel);
    font-size: 1.4rem;
    font-weight: 400;
    margin: 0;
    color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
    letter-spacing: 0.04em;
    text-transform: lowercase;
  }
  .tagline {
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }

  .btn {
    border: none;
    border-radius: 4px;
    font-family: var(--font-pixel);
    font-weight: 400;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
    padding: 9px 14px;
    transition: background 0.15s, box-shadow 0.15s, filter 0.15s;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .btn-primary {
    background: var(--accent);
    color: #000;
    box-shadow: var(--button-glow);
  }
  .btn-primary:hover { filter: brightness(1.1); }
  .btn-ghost {
    background: transparent;
    color: var(--text-muted);
    border: 1px solid var(--border);
  }
  .btn-ghost:hover { background: var(--bg-tertiary); color: var(--phosphor); border-color: var(--phosphor); }
  .plus {
    font-size: 1rem;
    font-weight: 700;
    line-height: 1;
  }

  .page-body {
    flex: 1;
    padding: 24px 28px;
    overflow-y: auto;
  }

  .title-row {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 16px;
  }
  .title-row h2 {
    font-family: var(--font-pixel);
    font-size: 0.875rem;
    font-weight: 400;
    margin: 0;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }
  .count {
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--text-muted);
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 14px;
  }

  .empty {
    display: flex;
    flex-direction: column;
    gap: 14px;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
    padding: 60px 20px;
    border: 1px dashed var(--border);
    border-radius: 6px;
    background: var(--bg-secondary);
    font-family: var(--font-mono);
  }
  .empty.error {
    color: var(--red);
    border-color: var(--red);
  }
  .empty p { margin: 0; }
</style>
