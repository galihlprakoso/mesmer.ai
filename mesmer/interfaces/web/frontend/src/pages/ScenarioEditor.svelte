<script>
  import { onMount } from 'svelte'
  import { loadScenario, createScenario, updateScenario } from '../lib/api.js'
  import { navigate } from '../lib/router.js'
  import ScenarioForm from '../components/ScenarioForm.svelte'
  import EditorChat from '../components/EditorChat.svelte'

  /** Either an existing scenario path (edit mode) or null (new mode). */
  export let path = null

  const BLANK_YAML = `name: My new scenario
description: ""
target:
  adapter: openai
  base_url: https://openrouter.ai/api/v1
  model: openai/gpt-4o-mini
  api_key: \${OPENROUTER_API_KEY}
objective:
  goal: ""
  max_turns: 20
modules:
  - system-prompt-extraction
agent:
  model: anthropic/claude-opus-4-7
  sub_module_model: anthropic/claude-haiku-4-5
  api_key: \${ANTHROPIC_API_KEY}
  temperature: 0.7
`

  let yamlContent = BLANK_YAML
  let displayName = path ? path.split('/').pop() : 'New scenario'
  let savedPath = path
  let saving = false
  let saveError = null
  let dirty = false
  let loading = !!path

  // Track the path the user is editing in this session (may change after
  // first save when creating from new). Drives the "Open graph view" button.
  $: hasSavedPath = !!savedPath

  async function bootstrap() {
    if (!path) {
      yamlContent = BLANK_YAML
      loading = false
      return
    }
    loading = true
    saveError = null
    try {
      const data = await loadScenario(path)
      if (data.yaml_content) {
        yamlContent = data.yaml_content
      }
      if (data.name) {
        displayName = data.name
      }
    } catch (e) {
      saveError = `Failed to load: ${e.message}`
    } finally {
      loading = false
      dirty = false
    }
  }

  onMount(bootstrap)

  function onYamlChange(event) {
    yamlContent = event.detail
    dirty = true
  }

  function onChatApply(event) {
    yamlContent = event.detail
    dirty = true
  }

  async function save() {
    saving = true
    saveError = null
    try {
      if (savedPath) {
        await updateScenario(savedPath, yamlContent)
      } else {
        // Pull the name out of the YAML for the create call.
        const nameMatch = yamlContent.match(/^\s*name:\s*['"]?([^'"\n]+?)['"]?\s*$/m)
        const name = nameMatch ? nameMatch[1].trim() : 'Untitled scenario'
        const result = await createScenario(name, yamlContent)
        savedPath = result.path
        displayName = result.name
        // Update the URL so a refresh hits the edit route directly.
        history.replaceState(null, '', `#/scenarios/${encodeURIComponent(savedPath)}/edit`)
      }
      dirty = false
    } catch (e) {
      saveError = e.message
    } finally {
      saving = false
    }
  }

  function backToList() {
    if (dirty && !confirm('Discard unsaved changes?')) return
    navigate('list')
  }

  function openGraph() {
    if (!savedPath) return
    if (dirty && !confirm('Discard unsaved changes and open graph view?')) return
    navigate('graph', savedPath)
  }
</script>

<div class="page">
  <header class="page-header">
    <button class="link-btn" type="button" on:click={backToList}>← Scenarios</button>
    <div class="title-block">
      <h1 title={savedPath || ''}>{displayName}</h1>
      {#if savedPath}
        <span class="path">{savedPath}</span>
      {:else}
        <span class="path new">unsaved</span>
      {/if}
    </div>
    <div class="actions">
      {#if saveError}
        <span class="save-err">{saveError}</span>
      {/if}
      <button
        class="btn btn-ghost"
        type="button"
        on:click={openGraph}
        disabled={!hasSavedPath}
        title={hasSavedPath ? 'Open the attack graph for this scenario' : 'Save once to enable'}
      >Open graph view</button>
      <button class="btn btn-primary" type="button" on:click={save} disabled={saving}>
        {saving ? 'Saving…' : (dirty ? 'Save *' : 'Save')}
      </button>
    </div>
  </header>

  {#if loading}
    <div class="loading">Loading scenario…</div>
  {:else}
    <main class="page-body">
      <ScenarioForm bind:yamlContent on:change={onYamlChange} disabled={saving} />
      <EditorChat {yamlContent} on:apply={onChatApply} />
    </main>
  {/if}
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
    gap: 18px;
    padding: 12px 18px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-secondary);
    flex-shrink: 0;
  }

  .link-btn {
    background: transparent;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 0.85rem;
    font-weight: 500;
    padding: 4px 8px;
    border-radius: 4px;
  }
  .link-btn:hover { color: var(--text); background: var(--bg-tertiary); }

  .title-block {
    display: flex;
    flex-direction: column;
    gap: 1px;
    flex: 1;
    overflow: hidden;
  }
  .title-block h1 {
    font-size: 0.95rem;
    font-weight: 600;
    margin: 0;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .path {
    font-family: var(--mono);
    font-size: 0.7rem;
    color: var(--text-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .path.new { color: var(--amber); }

  .actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .btn {
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
  }
  .btn-primary {
    background: var(--accent);
    color: #000;
  }
  .btn-primary:hover:not(:disabled) { background: var(--accent-hover); }
  .btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-ghost {
    background: transparent;
    color: var(--text-muted);
    border: 1px solid var(--border);
  }
  .btn-ghost:hover:not(:disabled) {
    color: var(--accent);
    border-color: var(--accent);
  }
  .btn-ghost:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .save-err {
    font-size: 0.75rem;
    color: var(--red);
    font-family: var(--mono);
    max-width: 280px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .page-body {
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 420px;
    min-height: 0;
  }

  @media (max-width: 1080px) {
    .page-body {
      grid-template-columns: 1fr 360px;
    }
  }

  .loading {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
    font-size: 0.9rem;
  }
</style>
