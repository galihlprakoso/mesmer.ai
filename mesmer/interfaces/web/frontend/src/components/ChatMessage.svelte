<script>
  export let message

  function formatTime(ts) {
    if (!ts) return ''
    const d = new Date(ts * 1000)
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  $: icon = message.sender === 'human' ? '👤' : message.sender === 'agent' ? '🤖' : '⚙'
  $: label = message.sender === 'human' ? 'YOU'
           : message.sender === 'agent' ? 'AGENT'
           : 'SYSTEM'
  $: subLabel = message.kind === 'agent-reflection' ? 'reflection'
             : message.kind === 'agent-outcome' ? 'outcome'
             : message.kind === 'agent-status' ? 'status'
             : ''
</script>

<div class="msg {message.sender} {message.kind}" class:dead={message.status === 'dead'} class:promising={message.status === 'promising'}>
  <div class="header">
    <span class="icon">{icon}</span>
    <span class="label">{label}</span>
    {#if subLabel}<span class="sub-label">({subLabel})</span>{/if}
    <span class="time">{formatTime(message.timestamp)}</span>
  </div>
  <div class="body">{message.text}</div>
</div>

<style>
  .msg {
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 0.82rem;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    max-width: 90%;
  }

  .msg.human {
    background: #f59e0b12;
    border-left: 3px solid var(--amber);
    align-self: flex-end;
  }

  .msg.agent {
    background: var(--bg-tertiary);
    border-left: 3px solid var(--accent);
  }

  .msg.system {
    background: transparent;
    border: 1px dashed var(--border);
    color: var(--text-muted);
    align-self: center;
    font-size: 0.75rem;
    font-style: italic;
    max-width: 70%;
    text-align: center;
  }

  .msg.dead { border-left-color: var(--red); }
  .msg.promising { border-left-color: var(--green); }

  .header {
    display: flex;
    align-items: baseline;
    gap: 6px;
    margin-bottom: 3px;
    font-size: 0.68rem;
  }

  .icon {
    font-size: 0.85rem;
  }

  .label {
    font-weight: 700;
    letter-spacing: 0.06em;
    color: var(--text-muted);
  }

  .sub-label {
    color: var(--text-muted);
    font-size: 0.65rem;
    text-transform: lowercase;
  }

  .time {
    color: var(--text-muted);
    font-size: 0.65rem;
    opacity: 0.7;
    margin-left: auto;
  }

  .body {
    color: var(--text);
  }

  .msg.system .body {
    color: var(--text-muted);
  }
</style>
