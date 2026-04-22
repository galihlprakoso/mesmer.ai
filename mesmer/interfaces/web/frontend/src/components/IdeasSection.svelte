<script>
  import { frontierNodes } from '../lib/stores.js'
  import IdeaItem from './IdeaItem.svelte'

  let humanExpanded = true
  let agentExpanded = false

  $: humanHints = $frontierNodes.filter(n => n.source === 'human')
  $: agentIdeas = $frontierNodes.filter(n => n.source !== 'human')
</script>

{#if humanHints.length + agentIdeas.length > 0}
  <div class="ideas">
    <div class="group">
      <button class="group-header" on:click={() => humanExpanded = !humanExpanded}>
        <span class="caret" class:expanded={humanExpanded}>▸</span>
        <span class="group-name human-color">Your hints</span>
        <span class="count">{humanHints.length}</span>
      </button>
      {#if humanExpanded && humanHints.length > 0}
        <div class="group-body">
          {#each humanHints as node (node.id)}
            <IdeaItem {node} variant="human" />
          {/each}
        </div>
      {:else if humanExpanded && humanHints.length === 0}
        <div class="empty">No hints yet — type below to add one.</div>
      {/if}
    </div>

    <div class="group">
      <button class="group-header" on:click={() => agentExpanded = !agentExpanded}>
        <span class="caret" class:expanded={agentExpanded}>▸</span>
        <span class="group-name agent-color">Agent's ideas</span>
        <span class="count">{agentIdeas.length}</span>
      </button>
      {#if agentExpanded && agentIdeas.length > 0}
        <div class="group-body">
          {#each agentIdeas as node (node.id)}
            <IdeaItem {node} variant="agent" />
          {/each}
        </div>
      {:else if agentExpanded && agentIdeas.length === 0}
        <div class="empty">No agent suggestions yet.</div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .ideas {
    border-top: 1px solid var(--border);
    padding: 8px 12px;
    background: var(--bg-secondary);
    max-height: 260px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .group {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .group-header {
    display: flex;
    align-items: center;
    gap: 6px;
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 2px 0;
    cursor: pointer;
  }

  .group-header:hover {
    color: var(--text);
  }

  .caret {
    display: inline-block;
    transition: transform 0.15s;
    width: 10px;
    font-size: 0.65rem;
  }

  .caret.expanded {
    transform: rotate(90deg);
  }

  .group-name.human-color { color: var(--amber); }
  .group-name.agent-color { color: var(--blue); }

  .count {
    background: var(--bg-tertiary);
    padding: 1px 6px;
    border-radius: 10px;
    font-size: 0.65rem;
    color: var(--text-muted);
  }

  .group-body {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding-left: 16px;
  }

  .empty {
    padding-left: 16px;
    font-size: 0.72rem;
    color: var(--text-muted);
    font-style: italic;
  }
</style>
