<script>
  import { afterUpdate, onMount, tick } from 'svelte'
  import {
    chatMessages, selectedScenario,
    isRunning, pendingQuestion,
    chatHistory, scratchpadDoc, scratchpadExists,
    scratchpadDrawerOpen,
  } from '../lib/stores.js'
  import { fetchChat, fetchScratchpad, sendLeaderChat } from '../lib/scratchpad-client.js'
  import ChatMessage from './ChatMessage.svelte'
  import HumanQuestion from './HumanQuestion.svelte'

  let messageText = ''
  let sending = false
  let feedback = null
  let feedbackTimer
  let scrollEl
  let autoScroll = true
  let toolTrace = []  // tool-call markers from the most recent reply

  $: inputEnabled = !!$selectedScenario

  // Warm-load chat + scratchpad when the scenario changes.
  async function loadForScenario() {
    if (!$selectedScenario) {
      chatHistory.set([])
      scratchpadDoc.set('')
      scratchpadExists.set(false)
      return
    }
    try {
      const [chat, sp] = await Promise.all([
        fetchChat($selectedScenario, 20),
        fetchScratchpad($selectedScenario),
      ])
      chatHistory.set(chat.items || [])
      scratchpadDoc.set(sp.content || '')
      scratchpadExists.set(!!sp.exists)
    } catch (e) {
      console.error('Failed to warm-load chat/scratchpad:', e)
    }
  }
  $: if ($selectedScenario) { loadForScenario() }
  onMount(loadForScenario)

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
    toolTrace = []

    // Optimistic append so the user sees their message immediately. The
    // server appends to chat.jsonl too; on next scenario reload we
    // dedupe via timestamp ordering.
    const ts = Date.now() / 1000
    chatHistory.update(h => [...h, { role: 'user', content: text, timestamp: ts }])
    messageText = ''
    await tick()
    if (scrollEl) scrollEl.scrollTop = scrollEl.scrollHeight

    try {
      const res = await sendLeaderChat($selectedScenario, text)
      if (res.queued) {
        showFeedback('ok', 'Queued for the live leader')
      } else if (res.reply) {
        chatHistory.update(h => [...h, {
          role: 'assistant', content: res.reply, timestamp: Date.now() / 1000,
        }])
        toolTrace = res.tool_trace || []
        if (res.updated_scratchpad !== undefined && res.updated_scratchpad !== null) {
          scratchpadDoc.set(res.updated_scratchpad)
          scratchpadExists.set(true)
          showFeedback('ok', 'Scratchpad updated')
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
    <div class="title">
      <span class="ico">💬</span>
      <span>Talk to the leader</span>
    </div>
    <button
      class="scratchpad-btn"
      class:has-content={$scratchpadExists}
      on:click={() => $scratchpadDrawerOpen = true}
      title="Open scratchpad"
      aria-label="Open scratchpad"
    >
      <span class="ico">📝</span>
      <span class="lbl">Scratchpad</span>
    </button>
  </div>

  <div class="messages" bind:this={scrollEl} on:scroll={onScroll}>
    {#if $chatMessages.length === 0}
      <div class="empty">
        {#if !$selectedScenario}
          Select a scenario to begin.
        {:else}
          No messages yet. Ask the leader anything — it can search the graph,
          past runs, leaked info, and rewrite the scratchpad.
        {/if}
      </div>
    {:else}
      {#each $chatMessages as m, i (i)}
        <ChatMessage message={m} />
      {/each}
    {/if}

    {#if toolTrace.length > 0}
      <div class="tool-trace">
        {#each toolTrace as call}
          <span class="trace-chip" title={call.result_preview || ''}>
            🔍 {call.name}
          </span>
        {/each}
      </div>
    {/if}
  </div>

  {#if $pendingQuestion}
    <HumanQuestion question={$pendingQuestion} />
  {/if}

  <div class="input-area" class:disabled={!inputEnabled}>
    {#if $isRunning}
      <div class="mode-hint">
        <span class="running-tag">RUN ACTIVE — your message is queued for the live leader</span>
      </div>
    {/if}
    <div class="input-row">
      <textarea
        bind:value={messageText}
        on:keydown={onKeydown}
        placeholder={inputEnabled
          ? ($isRunning
              ? 'Send a steer to the live leader... (Enter to send)'
              : 'Ask the leader about this target... (Enter to send)')
          : 'Select a scenario first'}
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
    gap: 8px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-primary);
  }

  .title {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--text-muted);
    font-family: var(--mono);
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .title .ico { font-size: 0.85rem; }

  .scratchpad-btn {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 10px;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text-muted);
    font-size: 0.72rem;
    font-weight: 600;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    transition: color 100ms, border-color 100ms;
  }
  .scratchpad-btn:hover { color: var(--accent); border-color: var(--accent); }
  .scratchpad-btn.has-content { color: var(--text); }
  .scratchpad-btn .ico { font-size: 0.85rem; }

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
    text-align: center;
    padding: 0 16px;
    line-height: 1.55;
  }

  .tool-trace {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding-top: 4px;
  }
  .trace-chip {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text-muted);
    font-family: var(--mono);
    font-size: 0.66rem;
    padding: 2px 6px;
    border-radius: 3px;
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
    flex-wrap: wrap;
  }

  .hint-icon { font-size: 0.8rem; }

  .running-tag {
    background: hsla(155 100% 42% / 0.12);
    border: 1px solid hsla(155 100% 42% / 0.5);
    color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
    padding: 1px 6px;
    border-radius: 3px;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

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
