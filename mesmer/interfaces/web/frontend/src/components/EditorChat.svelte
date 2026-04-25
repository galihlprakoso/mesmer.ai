<script>
  import { createEventDispatcher, tick } from 'svelte'
  import { editorChat } from '../lib/api.js'

  /** @type {string} The current YAML in the editor (read-only here). */
  export let yamlContent = ''

  const dispatch = createEventDispatcher()

  let messages = [] // [{role: 'user'|'assistant', content, ts}]
  let yamlHistory = [] // undo stack: previous YAML strings, capped
  const HISTORY_CAP = 20

  let input = ''
  let sending = false
  let error = null
  let scrollContainer

  async function send() {
    const msg = input.trim()
    if (!msg || sending) return
    input = ''
    sending = true
    error = null
    const userTurn = { role: 'user', content: msg, ts: Date.now() }
    messages = [...messages, userTurn]
    await scrollToBottom()
    try {
      const history = messages.slice(0, -1).map(m => ({ role: m.role, content: m.content }))
      const result = await editorChat(yamlContent, msg, history)
      messages = [...messages, { role: 'assistant', content: result.reply || '', ts: Date.now() }]
      if (typeof result.updated_yaml === 'string') {
        // Push current yaml onto undo stack BEFORE replacing.
        yamlHistory = [...yamlHistory, yamlContent].slice(-HISTORY_CAP)
        dispatch('apply', result.updated_yaml)
      }
    } catch (e) {
      error = e.message || 'Editor chat failed'
      messages = [...messages, { role: 'assistant', content: `⚠ ${error}`, ts: Date.now() }]
    } finally {
      sending = false
      await scrollToBottom()
    }
  }

  function undo() {
    if (yamlHistory.length === 0) return
    const last = yamlHistory[yamlHistory.length - 1]
    yamlHistory = yamlHistory.slice(0, -1)
    dispatch('apply', last)
    messages = [...messages, { role: 'assistant', content: '↶ reverted last change', ts: Date.now() }]
  }

  function clear() {
    messages = []
    yamlHistory = []
    error = null
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  async function scrollToBottom() {
    await tick()
    if (scrollContainer) {
      scrollContainer.scrollTop = scrollContainer.scrollHeight
    }
  }
</script>

<div class="chat-host">
  <div class="chat-header">
    <h3>Vibe-code</h3>
    <div class="chat-actions">
      <button
        type="button"
        class="icon-btn"
        on:click={undo}
        disabled={yamlHistory.length === 0}
        title="Undo last YAML change"
      >↶ Undo ({yamlHistory.length})</button>
      <button type="button" class="icon-btn" on:click={clear} title="Clear chat">Clear</button>
    </div>
  </div>

  <div class="chat-stream" bind:this={scrollContainer}>
    {#if messages.length === 0}
      <div class="empty">
        <p>Describe the scenario you want and I’ll edit the YAML.</p>
        <p class="hint">e.g. "Switch to <code>cognitive-overload</code> as the leader and bump <code>max_turns</code> to 30."</p>
      </div>
    {:else}
      {#each messages as m, i (i)}
        <div class="bubble" class:from-user={m.role === 'user'} class:from-agent={m.role === 'assistant'}>
          {m.content}
        </div>
      {/each}
    {/if}
    {#if sending}
      <div class="bubble from-agent typing">…</div>
    {/if}
  </div>

  <div class="chat-input">
    <textarea
      placeholder="Tell me how to edit the scenario…"
      bind:value={input}
      on:keydown={onKey}
      disabled={sending}
      rows="2"
    ></textarea>
    <button class="btn-send" on:click={send} disabled={sending || !input.trim()} type="button">
      {sending ? '…' : 'Send'}
    </button>
  </div>
</div>

<style>
  .chat-host {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--bg-secondary);
    overflow: hidden;
  }

  .chat-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-primary);
    flex-shrink: 0;
  }
  .chat-header h3 {
    margin: 0;
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .chat-actions { display: flex; gap: 6px; }
  .icon-btn {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-muted);
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 0.7rem;
    cursor: pointer;
    font-family: var(--mono);
  }
  .icon-btn:hover:not(:disabled) {
    color: var(--accent);
    border-color: var(--accent);
  }
  .icon-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .chat-stream {
    flex: 1;
    overflow-y: auto;
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .empty {
    color: var(--text-muted);
    font-size: 0.85rem;
    line-height: 1.5;
    padding: 18px;
    border: 1px dashed var(--border);
    border-radius: 8px;
    background: var(--bg-tertiary);
  }
  .empty p { margin: 0 0 8px 0; }
  .empty p:last-child { margin: 0; }
  .empty .hint { font-size: 0.78rem; color: var(--text-muted); }
  code {
    font-family: var(--mono);
    background: var(--bg-primary);
    padding: 1px 4px;
    border-radius: 3px;
  }

  .bubble {
    padding: 10px 12px;
    border-radius: 10px;
    font-size: 0.85rem;
    line-height: 1.45;
    max-width: 90%;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .from-user {
    background: var(--accent-dim);
    border: 1px solid var(--accent);
    color: var(--text);
    align-self: flex-end;
  }
  .from-agent {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text);
    align-self: flex-start;
  }
  .typing { color: var(--text-muted); }

  .chat-input {
    display: flex;
    gap: 8px;
    padding: 10px;
    border-top: 1px solid var(--border);
    background: var(--bg-primary);
    flex-shrink: 0;
  }
  textarea {
    flex: 1;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    padding: 8px 10px;
    font-family: inherit;
    font-size: 0.85rem;
    resize: none;
  }
  textarea:focus {
    outline: none;
    border-color: var(--accent);
  }
  .btn-send {
    background: var(--accent);
    color: #000;
    border: none;
    border-radius: 6px;
    padding: 0 16px;
    font-weight: 600;
    cursor: pointer;
    font-size: 0.85rem;
  }
  .btn-send:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
</style>
