<script>
  import { selectedScenario } from '../lib/stores.js'

  export let node              // AttackNode from the graph
  export let variant = 'agent'  // 'agent' | 'human'

  let editing = false
  let draft = ''
  let busy = false
  let error = null

  function startEdit() {
    draft = node.approach || ''
    editing = true
    error = null
  }

  function cancelEdit() {
    editing = false
    draft = ''
    error = null
  }

  async function saveEdit() {
    if (!draft.trim() || !$selectedScenario) return
    busy = true
    error = null
    try {
      const res = await fetch(`/api/frontier/${node.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario_path: $selectedScenario, approach: draft.trim() }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        error = data.error || `HTTP ${res.status}`
        return
      }
      editing = false
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }

  async function skip() {
    if (!$selectedScenario || busy) return
    busy = true
    error = null
    try {
      const res = await fetch(`/api/frontier/${node.id}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario_path: $selectedScenario }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        error = data.error || `HTTP ${res.status}`
      }
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }
</script>

<div class="idea {variant}" class:busy>
  {#if editing}
    <textarea
      bind:value={draft}
      rows="3"
      placeholder="Edit the approach..."
      disabled={busy}
    ></textarea>
    <div class="edit-actions">
      <button class="save" on:click={saveEdit} disabled={busy || !draft.trim()}>Save</button>
      <button class="cancel" on:click={cancelEdit} disabled={busy}>Cancel</button>
    </div>
  {:else}
    <div class="content">
      {#if variant === 'agent'}
        <span class="module">{node.module}</span>
      {/if}
      <span class="approach">{node.approach || '(no approach)'}</span>
    </div>
    <div class="actions">
      <button class="icon-btn" title="Edit" on:click={startEdit} disabled={busy}>✎</button>
      <button class="icon-btn danger" title="Skip (mark dead)" on:click={skip} disabled={busy}>✗</button>
    </div>
  {/if}
  {#if error}
    <div class="error">{error}</div>
  {/if}
</div>

<style>
  .idea {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 6px 8px;
    border-radius: 4px;
    font-size: 0.78rem;
    border-left: 2px solid var(--blue);
    background: var(--bg-tertiary);
  }

  .idea.human {
    border-left-color: var(--amber);
  }

  .idea.busy {
    opacity: 0.5;
  }

  .content {
    flex: 1;
    min-width: 0;
    line-height: 1.4;
  }

  .module {
    color: var(--accent);
    font-weight: 600;
    font-size: 0.7rem;
    margin-right: 6px;
  }

  .approach {
    color: var(--text);
  }

  .actions {
    display: flex;
    gap: 2px;
    flex-shrink: 0;
  }

  .icon-btn {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 0.9rem;
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 3px;
    line-height: 1;
  }

  .icon-btn:hover:not(:disabled) {
    background: var(--bg-secondary);
    color: var(--text);
  }

  .icon-btn.danger:hover:not(:disabled) {
    color: var(--red);
  }

  .icon-btn:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }

  textarea {
    flex: 1;
    padding: 6px;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text);
    font-size: 0.78rem;
    font-family: inherit;
    resize: vertical;
  }

  textarea:focus {
    outline: 1px solid var(--accent);
    border-color: var(--accent);
  }

  .edit-actions {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .save, .cancel {
    padding: 4px 8px;
    border: none;
    border-radius: 3px;
    font-size: 0.72rem;
    cursor: pointer;
    font-weight: 600;
  }

  .save { background: var(--accent); color: #000; }
  .save:hover:not(:disabled) { background: var(--accent-hover); }
  .save:disabled { opacity: 0.4; cursor: not-allowed; }

  .cancel { background: transparent; color: var(--text-muted); border: 1px solid var(--border); }
  .cancel:hover:not(:disabled) { color: var(--text); }

  .error {
    flex: 100%;
    font-size: 0.7rem;
    color: var(--red);
    margin-top: 4px;
  }
</style>
