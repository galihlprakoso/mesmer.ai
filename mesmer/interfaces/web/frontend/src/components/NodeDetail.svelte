<script>
  import { selectedNode } from '../lib/stores.js'

  function close() {
    $selectedNode = null
  }

  function statusClass(status) {
    return status === 'dead' ? 'dead' : status === 'promising' ? 'promising' : status === 'frontier' ? 'frontier' : ''
  }
</script>

{#if $selectedNode}
  <aside class="right-sidebar">
    <div class="header">
      <div class="title-row">
        {#if $selectedNode.isGroup}
          <span class="status-badge {statusClass($selectedNode.bestStatus)}">
            {$selectedNode.bestStatus?.toUpperCase()} &middot; {$selectedNode.attemptCount} attempts
          </span>
          <span class="score">{$selectedNode.bestScore}/10</span>
        {:else}
          <span class="status-badge {statusClass($selectedNode.status)}" class:human={$selectedNode.source === 'human'}>
            {$selectedNode.source === 'human' ? 'HUMAN' : $selectedNode.status?.toUpperCase()}
          </span>
          {#if $selectedNode.score}
            <span class="score">{$selectedNode.score}/10</span>
          {/if}
        {/if}
      </div>
      <button class="close-btn" on:click={close}>&times;</button>
    </div>

    <h3 class="module-name">{$selectedNode.module || $selectedNode.id}</h3>

    <div class="scroll-area">
      {#if $selectedNode.isGroup}
        <!-- GROUP NODE: show all attempts -->
        <div class="field">
          <span class="field-label">Scores</span>
          <div class="score-pills">
            {#each $selectedNode.attempts as attempt, i}
              <span class="score-pill {statusClass(attempt.status)}">{attempt.score}/10</span>
            {/each}
          </div>
        </div>

        {#if $selectedNode.leaked_info}
          <div class="field highlight">
            <span class="field-label">Best Leaked Info</span>
            <p>{$selectedNode.leaked_info}</p>
          </div>
        {/if}

        {#each $selectedNode.attempts as attempt, i}
          <div class="attempt-card">
            <div class="attempt-header">
              <span class="attempt-num">Attempt {i + 1}</span>
              <span class="attempt-score {statusClass(attempt.status)}">{attempt.score}/10 {attempt.status}</span>
            </div>
            {#if attempt.approach}
              <p class="attempt-approach">{attempt.approach}</p>
            {/if}
            {#if attempt.leaked_info}
              <p class="attempt-leaked">{attempt.leaked_info}</p>
            {/if}
            {#if attempt.reflection}
              <p class="attempt-reflection">{attempt.reflection}</p>
            {/if}
            {#if attempt.messages_sent?.length}
              <div class="messages">
                {#each attempt.messages_sent as msg, j}
                  <div class="msg sent">
                    <span class="msg-label">SENT</span>
                    <p>{msg}</p>
                  </div>
                  {#if attempt.target_responses?.[j]}
                    <div class="msg recv">
                      <span class="msg-label">RECV</span>
                      <p>{attempt.target_responses[j]}</p>
                    </div>
                  {/if}
                {/each}
              </div>
            {/if}
          </div>
        {/each}

      {:else}
        <!-- SINGLE NODE -->
        {#if $selectedNode.approach}
          <div class="field">
            <span class="field-label">Approach</span>
            <p>{$selectedNode.approach}</p>
          </div>
        {/if}

        {#if $selectedNode.leaked_info}
          <div class="field highlight">
            <span class="field-label">Leaked Info</span>
            <p>{$selectedNode.leaked_info}</p>
          </div>
        {/if}

        {#if $selectedNode.reflection}
          <div class="field">
            <span class="field-label">Reflection</span>
            <p>{$selectedNode.reflection}</p>
          </div>
        {/if}

        {#if $selectedNode.messages_sent?.length}
          <div class="field">
            <span class="field-label">Messages ({$selectedNode.messages_sent.length})</span>
            <div class="messages">
              {#each $selectedNode.messages_sent as msg, i}
                <div class="msg sent">
                  <span class="msg-label">SENT</span>
                  <p>{msg}</p>
                </div>
                {#if $selectedNode.target_responses?.[i]}
                  <div class="msg recv">
                    <span class="msg-label">RECV</span>
                    <p>{$selectedNode.target_responses[i]}</p>
                  </div>
                {/if}
              {/each}
            </div>
          </div>
        {/if}
      {/if}

      <div class="meta">
        {#if $selectedNode.isGroup}
          <span>{$selectedNode.attemptCount} attempts</span>
        {:else}
          <span>ID: {$selectedNode.id?.slice(0, 12)}</span>
          <span>Depth: {$selectedNode.depth ?? '?'}</span>
          {#if $selectedNode.run_id}
            <span>Run: {$selectedNode.run_id}</span>
          {/if}
        {/if}
      </div>
    </div>
  </aside>
{/if}

<style>
  .right-sidebar {
    width: 340px;
    min-width: 340px;
    background: var(--bg-secondary);
    border-left: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    animation: slideIn 0.2s ease-out;
  }

  @keyframes slideIn {
    from { transform: translateX(40px); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }

  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 14px 16px 0;
  }

  .title-row { display: flex; align-items: center; gap: 8px; }

  .module-name {
    margin: 6px 16px 12px;
    font-size: 1rem;
    font-weight: 600;
    color: var(--text);
  }

  .status-badge {
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    background: var(--bg-tertiary);
    color: var(--text-muted);
  }

  .status-badge.dead { background: #ef44441a; color: var(--red); }
  .status-badge.promising { background: #22c55e1a; color: var(--green); }
  .status-badge.frontier { background: #3b82f61a; color: var(--blue); }
  .status-badge.human { background: #f59e0b1a; color: var(--amber); }

  .score { font-weight: 700; font-size: 1.1rem; color: var(--green); }

  .close-btn {
    background: none; border: none; color: var(--text-muted);
    font-size: 1.4rem; cursor: pointer; padding: 0 4px; line-height: 1;
  }
  .close-btn:hover { color: var(--text); }

  .scroll-area { flex: 1; overflow-y: auto; padding: 0 16px 16px; }

  .field { margin-bottom: 14px; }
  .field-label {
    font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--text-muted); display: block; margin-bottom: 4px;
  }
  .field p { margin: 0; font-size: 0.82rem; color: var(--text); line-height: 1.5; }
  .field.highlight {
    background: #22c55e08; border-left: 3px solid var(--green);
    padding: 8px 12px; border-radius: 0 6px 6px 0;
  }

  /* Score pills for groups */
  .score-pills { display: flex; gap: 4px; flex-wrap: wrap; }
  .score-pill {
    padding: 3px 8px; border-radius: 4px; font-size: 0.72rem; font-weight: 700;
    background: var(--bg-tertiary); color: var(--text-muted);
  }
  .score-pill.dead { color: var(--red); }
  .score-pill.promising { color: var(--green); }

  /* Attempt cards for groups */
  .attempt-card {
    border: 1px solid var(--border); border-radius: 6px;
    padding: 10px 12px; margin-bottom: 8px;
  }
  .attempt-header {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 6px;
  }
  .attempt-num { font-size: 0.7rem; font-weight: 600; color: var(--text-muted); }
  .attempt-score {
    font-size: 0.68rem; font-weight: 700; color: var(--text-muted);
    padding: 1px 6px; border-radius: 3px; background: var(--bg-tertiary);
  }
  .attempt-score.dead { color: var(--red); }
  .attempt-score.promising { color: var(--green); }

  .attempt-approach { font-size: 0.78rem; color: var(--text); margin: 0 0 4px; line-height: 1.4; }
  .attempt-leaked {
    font-size: 0.75rem; color: var(--green);
    border-left: 2px solid var(--green); padding-left: 6px;
    margin: 4px 0;
  }
  .attempt-reflection { font-size: 0.72rem; color: var(--text-muted); margin: 4px 0; font-style: italic; }

  /* Messages */
  .messages { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
  .msg { padding: 8px 10px; border-radius: 6px; font-size: 0.78rem; }
  .msg-label { font-size: 0.55rem; font-weight: 700; letter-spacing: 0.1em; display: block; margin-bottom: 3px; }
  .msg.sent { background: var(--bg-tertiary); border-left: 2px solid var(--cyan); }
  .msg.sent .msg-label { color: var(--cyan); }
  .msg.recv { background: var(--bg-tertiary); border-left: 2px solid var(--amber); }
  .msg.recv .msg-label { color: var(--amber); }
  .msg p { margin: 0; color: var(--text); white-space: pre-wrap; word-break: break-word; line-height: 1.5; }

  .meta {
    display: flex; flex-wrap: wrap; gap: 12px;
    font-size: 0.65rem; color: var(--text-muted);
    padding-top: 10px; border-top: 1px solid var(--border); font-family: monospace;
  }
</style>
