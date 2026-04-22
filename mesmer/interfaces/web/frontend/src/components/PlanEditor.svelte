<script>
  import { onMount } from 'svelte'
  import { selectedScenario, planDoc, planExists, mode } from '../lib/stores.js'
  import { fetchPlan, savePlan } from '../lib/plan-client.js'

  let editing = false
  let draft = ''
  let busy = false
  let feedback = null
  let feedbackTimer

  async function load() {
    if (!$selectedScenario) {
      $planDoc = ''
      $planExists = false
      return
    }
    try {
      const data = await fetchPlan($selectedScenario)
      $planDoc = data.content || ''
      $planExists = !!data.exists
    } catch (e) {
      console.error('Failed to load plan:', e)
    }
  }

  // Re-fetch when scenario changes
  $: if ($selectedScenario) { load() }

  onMount(load)

  function flash(type, text) {
    feedback = { type, text }
    clearTimeout(feedbackTimer)
    feedbackTimer = setTimeout(() => { feedback = null }, 2000)
  }

  function startEdit() {
    draft = $planDoc
    editing = true
  }

  function cancelEdit() {
    editing = false
    draft = ''
  }

  async function saveDraft() {
    if (!$selectedScenario || busy) return
    busy = true
    try {
      await savePlan($selectedScenario, draft)
      $planDoc = draft
      $planExists = !!draft.trim()
      editing = false
      flash('ok', 'Saved')
    } catch (e) {
      flash('err', e.message)
    } finally {
      busy = false
    }
  }

  async function clearPlan() {
    if (!$selectedScenario || busy) return
    if (!confirm('Delete the current plan.md?')) return
    busy = true
    try {
      await savePlan($selectedScenario, '')
      $planDoc = ''
      $planExists = false
      editing = false
      flash('ok', 'Plan deleted')
    } catch (e) {
      flash('err', e.message)
    } finally {
      busy = false
    }
  }
</script>

<div class="plan-editor" class:empty={!$planDoc && !editing}>
  <div class="header">
    <div class="title">
      <span class="icon">📄</span>
      <span class="name">plan.md</span>
      {#if $planExists}
        <span class="status-pill ok">saved</span>
      {:else}
        <span class="status-pill empty">none</span>
      {/if}
    </div>
    <div class="actions">
      {#if editing}
        <button class="btn save" on:click={saveDraft} disabled={busy}>Save</button>
        <button class="btn ghost" on:click={cancelEdit} disabled={busy}>Cancel</button>
      {:else}
        <button class="btn ghost" on:click={startEdit} disabled={busy}>Edit</button>
        {#if $planExists}
          <button class="btn danger" on:click={clearPlan} disabled={busy}>Delete</button>
        {/if}
      {/if}
      {#if feedback}
        <span class="feedback {feedback.type}">
          {feedback.type === 'ok' ? '✓' : '✗'} {feedback.text}
        </span>
      {/if}
    </div>
  </div>

  {#if editing}
    <textarea
      bind:value={draft}
      class="editor"
      placeholder="# Attack Plan&#10;&#10;Describe the angle, which modules to use, and anything the agent should know..."
      disabled={busy}
    ></textarea>
  {:else if $planDoc}
    <pre class="viewer">{$planDoc}</pre>
  {:else}
    <div class="empty-msg">
      No plan yet. Chat with the planner below to draft one, or click <strong>Edit</strong> to write directly.
      {#if $mode === 'plan'}
        <br/>Tip: ask questions like "What's the best angle for this target?" or "Help me avoid dead-ends from the last run."
      {/if}
    </div>
  {/if}
</div>

<style>
  .plan-editor {
    background: var(--bg-primary);
    border-bottom: 1px solid var(--border);
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 40%;
    min-height: 80px;
  }

  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
  }

  .title {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .icon { font-size: 0.95rem; }
  .name {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--text);
  }

  .status-pill {
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .status-pill.ok { background: #22c55e1a; color: var(--green); }
  .status-pill.empty { background: var(--bg-tertiary); color: var(--text-muted); }

  .actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .btn {
    padding: 4px 10px;
    border-radius: 4px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-muted);
    font-size: 0.72rem;
    font-weight: 600;
    cursor: pointer;
  }
  .btn:hover:not(:disabled) { color: var(--text); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }

  .btn.save { background: var(--accent); color: #000; border-color: var(--accent); }
  .btn.save:hover:not(:disabled) { background: var(--accent-hover); }

  .btn.danger:hover:not(:disabled) { color: var(--red); border-color: var(--red); }

  .feedback {
    font-size: 0.68rem;
    font-weight: 600;
  }
  .feedback.ok { color: var(--green); }
  .feedback.err { color: var(--red); }

  .editor, .viewer {
    flex: 1;
    min-height: 100px;
    max-height: 300px;
    overflow-y: auto;
    padding: 8px 10px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
    color: var(--text);
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
  }

  .editor {
    resize: vertical;
  }

  .editor:focus {
    outline: 1px solid var(--accent);
    border-color: var(--accent);
  }

  .empty-msg {
    font-size: 0.78rem;
    color: var(--text-muted);
    font-style: italic;
    padding: 12px 8px;
    line-height: 1.5;
  }
</style>
