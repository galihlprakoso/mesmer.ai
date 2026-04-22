<script>
  import { afterUpdate, tick } from 'svelte'
  import {
    mode, showTrace, chatMessages, events, selectedScenario,
    isRunning, runStatus, pendingQuestion,
    planChatMessages, planDoc, planExists,
  } from '../lib/stores.js'
  import { send } from '../lib/ws.js'
  import { planChat } from '../lib/plan-client.js'
  import ChatMessage from './ChatMessage.svelte'
  import IdeasSection from './IdeasSection.svelte'
  import HumanQuestion from './HumanQuestion.svelte'
  import PlanEditor from './PlanEditor.svelte'

  let messageText = ''
  let sending = false
  let feedback = null
  let feedbackTimer
  let scrollEl
  let autoScroll = true

  $: modeDescription = {
    'plan':       'Draft the attack plan with the agent before running.',
    'co-op':      'Agent pauses to ask you questions during the run.',
    'autonomous': 'Agent runs without pausing. Your messages are added as hints.',
  }[$mode]

  $: modeReady = true   // all three modes are wired up
  $: inPlanMode = $mode === 'plan'
  $: inputEnabled = modeReady && $selectedScenario
  $: visibleMessages = inPlanMode ? $planChatMessages : $chatMessages

  // Auto-scroll to bottom when new messages arrive, unless user scrolled up
  afterUpdate(() => {
    if (autoScroll && scrollEl) {
      scrollEl.scrollTop = scrollEl.scrollHeight
    }
  })

  function onScroll() {
    if (!scrollEl) return
    const nearBottom = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight < 40
    autoScroll = nearBottom
  }

  function showFeedback(type, msg) {
    feedback = { type, text: msg }
    clearTimeout(feedbackTimer)
    feedbackTimer = setTimeout(() => { feedback = null }, 2200)
  }

  async function submit() {
    const text = messageText.trim()
    if (!text || !inputEnabled) return
    sending = true

    try {
      if (inPlanMode) {
        // Plan mode: chat with the planner LLM about plan.md
        const now = Date.now() / 1000
        const userMsg = {
          role: 'user', content: text, timestamp: now,
          // fields consumed by ChatMessage.svelte
          sender: 'human', kind: 'human', text,
        }
        $planChatMessages = [...$planChatMessages, userMsg]
        messageText = ''
        await tick()
        if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight

        // Strip ChatMessage-only fields before sending
        const payload = $planChatMessages.map(m => ({ role: m.role, content: m.content }))
        try {
          const res = await planChat($selectedScenario, payload)
          const reply = {
            role: 'assistant', content: res.reply || '', timestamp: Date.now() / 1000,
            sender: 'agent', kind: 'agent-status', text: res.reply || '',
          }
          $planChatMessages = [...$planChatMessages, reply]
          if (res.updated_plan !== undefined && res.updated_plan !== null) {
            $planDoc = res.updated_plan
            $planExists = !!res.updated_plan.trim()
            showFeedback('ok', 'Plan updated')
          }
        } catch (e) {
          showFeedback('err', e.message)
        }
      } else if ($isRunning) {
        send({ type: 'hint', text, scenario_path: $selectedScenario })
        messageText = ''
        showFeedback('ok', 'Injected')
      } else {
        const res = await fetch('/api/hint', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scenario_path: $selectedScenario, text }),
        })
        if (!res.ok) {
          const data = await res.json().catch(() => ({}))
          showFeedback('err', data.error || `HTTP ${res.status}`)
        } else {
          messageText = ''
          showFeedback('ok', 'Saved')
          await tick()
          if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight
        }
      }
    } catch (e) {
      showFeedback('err', e.message)
    } finally {
      sending = false
    }
  }

  function onKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }
</script>

<div class="chat-panel">
  <div class="top-bar">
    <div class="mode-selector">
      {#each ['plan', 'co-op', 'autonomous'] as m}
        <button
          class="mode-btn"
          class:active={$mode === m}
          on:click={() => $mode = m}
        >{m === 'co-op' ? 'Co-op' : m.charAt(0).toUpperCase() + m.slice(1)}</button>
      {/each}
    </div>

    <div class="top-actions">
      <button class="trace-btn" class:active={$showTrace} on:click={() => $showTrace = !$showTrace}>
        {$showTrace ? 'Hide trace' : 'Show trace'}
      </button>
      <span class="status-dot" class:running={$isRunning} class:error={$runStatus === 'error'}></span>
      <span class="status-text">{$runStatus}</span>
    </div>
  </div>

  {#if inPlanMode}
    <PlanEditor />
  {/if}

  <div class="messages" bind:this={scrollEl} on:scroll={onScroll}>
    {#if visibleMessages.length === 0}
      <div class="empty">
        {#if !$selectedScenario}
          Select a scenario to begin.
        {:else if inPlanMode}
          Chat with the planner to draft your attack plan. Ask questions, propose angles, review prior runs.
        {:else}
          No messages yet. Start an attack or add a hint to begin the conversation.
        {/if}
      </div>
    {:else}
      {#each visibleMessages as m, i (i)}
        <ChatMessage message={m} />
      {/each}
    {/if}

    {#if $showTrace && $events.length > 0 && !inPlanMode}
      <div class="trace">
        <div class="trace-header">Trace (raw events)</div>
        {#each $events as evt}
          <div class="trace-row">
            <span class="trace-event">{evt.event || evt.status}</span>
            <span class="trace-detail">{evt.detail || evt.result || ''}</span>
          </div>
        {/each}
      </div>
    {/if}
  </div>

  {#if !inPlanMode}
    <IdeasSection />

    {#if $pendingQuestion}
      <HumanQuestion question={$pendingQuestion} />
    {/if}
  {/if}

  <div class="input-area" class:disabled={!inputEnabled}>
    <div class="mode-hint">
      <span class="hint-icon">{modeReady ? '💡' : '🚧'}</span>
      <span>
        {modeDescription}
        {#if !modeReady}<em>&nbsp;— not yet wired up.</em>{/if}
      </span>
    </div>
    <div class="input-row">
      <textarea
        bind:value={messageText}
        on:keydown={onKeydown}
        placeholder={inputEnabled ? 'Type a message to the agent... (Enter to send, Shift+Enter for newline)' : (modeReady ? 'Select a scenario first' : 'Coming soon')}
        disabled={!inputEnabled || sending}
        rows="2"
      ></textarea>
      <button class="send-btn" on:click={submit} disabled={!inputEnabled || !messageText.trim() || sending}>
        {#if sending}...{:else}Send{/if}
      </button>
    </div>
    {#if feedback}
      <div class="feedback {feedback.type}">
        {feedback.type === 'ok' ? '✓' : '✗'} {feedback.text}
      </div>
    {/if}
  </div>
</div>

<style>
  .chat-panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--bg-secondary);
  }

  .top-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-primary);
  }

  .mode-selector {
    display: flex;
    gap: 2px;
    background: var(--bg-tertiary);
    border-radius: 6px;
    padding: 2px;
  }

  .mode-btn {
    padding: 5px 12px;
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-size: 0.72rem;
    font-weight: 600;
    border-radius: 4px;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    transition: background 0.15s, color 0.15s;
  }

  .mode-btn:hover { color: var(--text); }
  .mode-btn.active {
    background: var(--accent);
    color: #000;
  }

  .top-actions {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .trace-btn {
    padding: 4px 10px;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text-muted);
    font-size: 0.68rem;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .trace-btn:hover { color: var(--text); }
  .trace-btn.active { color: var(--accent); border-color: var(--accent); }

  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--text-muted);
  }
  .status-dot.running {
    background: var(--green);
    animation: pulse 1.5s infinite;
  }
  .status-dot.error { background: var(--red); }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  .status-text {
    font-size: 0.7rem;
    color: var(--text-muted);
    font-family: monospace;
  }

  .messages {
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .empty {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
    font-size: 0.85rem;
    font-style: italic;
  }

  .trace {
    margin-top: 16px;
    padding: 8px 12px;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 6px;
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.7rem;
  }

  .trace-header {
    font-weight: 700;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 6px;
    font-size: 0.65rem;
  }

  .trace-row {
    display: flex;
    gap: 8px;
    line-height: 1.5;
  }

  .trace-event {
    color: var(--blue);
    min-width: 100px;
    font-weight: 600;
  }

  .trace-detail {
    color: var(--text-muted);
    word-break: break-all;
  }

  .input-area {
    border-top: 1px solid var(--border);
    padding: 8px 12px;
    background: var(--bg-secondary);
  }

  .input-area.disabled .input-row { opacity: 0.5; }

  .mode-hint {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.68rem;
    color: var(--text-muted);
    margin-bottom: 6px;
  }

  .hint-icon { font-size: 0.8rem; }

  .input-row {
    display: flex;
    gap: 8px;
  }

  textarea {
    flex: 1;
    padding: 8px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-size: 0.82rem;
    font-family: inherit;
    resize: none;
  }

  textarea::placeholder { color: var(--text-muted); }
  textarea:focus:not(:disabled) { outline: 1px solid var(--accent); border-color: var(--accent); }
  textarea:disabled { cursor: not-allowed; }

  .send-btn {
    padding: 8px 16px;
    background: var(--accent);
    color: #000;
    border: none;
    border-radius: 6px;
    font-weight: 600;
    font-size: 0.82rem;
    cursor: pointer;
    align-self: flex-end;
    min-width: 70px;
  }

  .send-btn:hover:not(:disabled) { background: var(--accent-hover); }
  .send-btn:disabled { opacity: 0.3; cursor: not-allowed; }

  .feedback {
    margin-top: 4px;
    font-size: 0.7rem;
    font-weight: 600;
  }
  .feedback.ok { color: var(--green); }
  .feedback.err { color: var(--red); }
</style>
