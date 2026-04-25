<script>
  import { onMount } from 'svelte'
  import {
    selectedScenario,
    scratchpadDoc,
    scratchpadExists,
    scratchpadDrawerOpen,
  } from '../lib/stores.js'
  import { fetchScratchpad, saveScratchpad } from '../lib/scratchpad-client.js'

  let editing = false
  let draft = ''
  let busy = false
  let feedback = null
  let feedbackTimer

  async function load() {
    if (!$selectedScenario) {
      $scratchpadDoc = ''
      $scratchpadExists = false
      return
    }
    try {
      const data = await fetchScratchpad($selectedScenario)
      $scratchpadDoc = data.content || ''
      $scratchpadExists = !!data.exists
    } catch (e) {
      console.error('Failed to load scratchpad:', e)
    }
  }

  // Refetch when scenario changes OR when the drawer is opened (so a
  // mid-run update_scratchpad call is reflected without a manual refresh).
  $: if ($selectedScenario || $scratchpadDrawerOpen) { load() }

  onMount(load)

  function flash(type, text) {
    feedback = { type, text }
    clearTimeout(feedbackTimer)
    feedbackTimer = setTimeout(() => { feedback = null }, 1800)
  }

  function close() { $scratchpadDrawerOpen = false }
  function startEdit() { draft = $scratchpadDoc; editing = true }
  function cancelEdit() { editing = false; draft = '' }

  async function saveDraft() {
    if (!$selectedScenario || busy) return
    busy = true
    try {
      await saveScratchpad($selectedScenario, draft)
      $scratchpadDoc = draft
      $scratchpadExists = !!draft.trim()
      editing = false
      flash('ok', 'Saved')
    } catch (e) {
      flash('err', e.message)
    } finally {
      busy = false
    }
  }

  async function clearAll() {
    if (!$selectedScenario || busy) return
    if (!confirm('Delete the current scratchpad.md?')) return
    busy = true
    try {
      await saveScratchpad($selectedScenario, '')
      $scratchpadDoc = ''
      $scratchpadExists = false
      editing = false
      flash('ok', 'Deleted')
    } catch (e) {
      flash('err', e.message)
    } finally {
      busy = false
    }
  }
</script>

{#if $scratchpadDrawerOpen}
  <div class="overlay" on:click={close} role="presentation"></div>
  <aside class="drawer" role="dialog" aria-label="Scratchpad">
    <header class="drawer-header">
      <h2>
        <span class="ico">📝</span>
        <span class="name">scratchpad.md</span>
        {#if $scratchpadExists}
          <span class="pill ok">saved</span>
        {:else}
          <span class="pill empty">none</span>
        {/if}
      </h2>
      <div class="actions">
        {#if editing}
          <button class="btn save" on:click={saveDraft} disabled={busy}>Save</button>
          <button class="btn ghost" on:click={cancelEdit} disabled={busy}>Cancel</button>
        {:else}
          <button class="btn ghost" on:click={startEdit} disabled={busy}>Edit</button>
          {#if $scratchpadExists}
            <button class="btn danger" on:click={clearAll} disabled={busy}>Delete</button>
          {/if}
        {/if}
        <button class="btn close" on:click={close} aria-label="Close">&times;</button>
      </div>
    </header>

    {#if feedback}
      <div class="feedback {feedback.type}">{feedback.type === 'ok' ? '✓' : '✗'} {feedback.text}</div>
    {/if}

    <div class="body">
      {#if editing}
        <textarea
          bind:value={draft}
          placeholder={"# Scratchpad\n\nNotes about this target — refusal patterns, winning angles, dead-ends."}
          disabled={busy}
        ></textarea>
      {:else if $scratchpadDoc}
        <pre>{$scratchpadDoc}</pre>
      {:else}
        <div class="empty-msg">
          No scratchpad yet for this target.<br />
          Talk to the leader in chat to draft one — or click <strong>Edit</strong> to write directly.
        </div>
      {/if}
    </div>

    <footer class="drawer-footer">
      Loaded into the leader's prompt every iteration. Mid-run, the leader
      can rewrite this via the <code>update_scratchpad</code> tool — your
      drawer auto-refreshes.
    </footer>
  </aside>
{/if}

<style>
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 200;
    animation: fadeIn 0.15s ease-out;
  }
  .drawer {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    width: 480px;
    max-width: 90vw;
    background: var(--bg-secondary);
    border-left: 1px solid var(--border);
    z-index: 201;
    display: flex;
    flex-direction: column;
    animation: slideInRight 0.2s ease-out;
  }
  .drawer-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
  }
  .drawer-header h2 {
    margin: 0;
    flex: 1;
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--text);
  }
  .ico { font-size: 1.05rem; }
  .name {
    font-family: var(--mono);
    font-size: 0.82rem;
  }
  .pill {
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .pill.ok { background: rgba(34, 197, 94, 0.12); color: var(--green); }
  .pill.empty { background: var(--bg-tertiary); color: var(--text-muted); }
  .actions {
    display: flex;
    align-items: center;
    gap: 4px;
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
  .btn.close {
    border: none;
    font-size: 1.4rem;
    line-height: 1;
    padding: 0 6px;
  }
  .feedback {
    padding: 6px 14px;
    font-size: 0.72rem;
    font-weight: 600;
    border-bottom: 1px solid var(--border);
  }
  .feedback.ok { color: var(--green); background: rgba(34, 197, 94, 0.06); }
  .feedback.err { color: var(--red); background: rgba(239, 68, 68, 0.06); }
  .body {
    flex: 1;
    overflow: hidden;
    display: flex;
    padding: 12px 14px;
  }
  textarea, pre {
    flex: 1;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 10px 12px;
    font-family: var(--mono);
    font-size: 0.78rem;
    color: var(--text);
    line-height: 1.55;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
    overflow-y: auto;
    resize: none;
  }
  textarea:focus { outline: 1px solid var(--accent); border-color: var(--accent); }
  .empty-msg {
    flex: 1;
    color: var(--text-muted);
    font-size: 0.82rem;
    font-style: italic;
    line-height: 1.6;
    padding: 24px 8px;
    text-align: center;
  }
  .drawer-footer {
    padding: 8px 14px;
    border-top: 1px solid var(--border);
    font-size: 0.7rem;
    color: var(--text-muted);
    line-height: 1.5;
  }
  .drawer-footer code {
    font-family: var(--mono);
    background: var(--bg-tertiary);
    padding: 1px 4px;
    border-radius: 3px;
  }
  @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
  @keyframes slideInRight {
    from { transform: translateX(100%); }
    to { transform: translateX(0); }
  }
</style>
