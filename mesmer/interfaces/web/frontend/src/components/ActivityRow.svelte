<script>
  /**
   * Single row in the Activity feed. Renders a timestamp, icon, and narrative text.
   *
   * entry shape:
   *   { kind, time, title, body?, color }
   */
  export let entry

  function formatTime(ts) {
    if (!ts) return ''
    const d = new Date(ts * 1000)
    return d.toLocaleTimeString('en-US', {
      hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
  }
</script>

<div class="row {entry.kind}">
  <span class="time">{formatTime(entry.time)}</span>
  <span class="icon" style="color: {entry.color || 'var(--text-muted)'}">{entry.icon || '·'}</span>
  <div class="content">
    <div class="title" style="color: {entry.color || 'var(--text)'}">{entry.title}</div>
    {#if entry.body}
      <div class="body">{entry.body}</div>
    {/if}
  </div>
</div>

<style>
  .row {
    display: grid;
    grid-template-columns: 58px 18px 1fr;
    gap: 6px;
    padding: 6px 10px;
    font-size: 0.76rem;
    line-height: 1.4;
    border-bottom: 1px solid rgba(255,255,255,0.03);
  }

  .time {
    color: var(--text-muted);
    font-size: 0.65rem;
    opacity: 0.6;
    font-family: var(--font-mono);
    padding-top: 2px;
  }

  .body {
    font-family: var(--font-mono);
  }

  .icon {
    text-align: center;
    font-weight: 700;
    padding-top: 1px;
  }

  .content {
    min-width: 0;
  }

  .title {
    font-weight: 600;
    margin-bottom: 2px;
  }

  .body {
    color: var(--text);
    word-break: break-word;
    white-space: pre-wrap;
    font-size: 0.74rem;
    opacity: 0.85;
  }

  .row.send .body { color: var(--cyan); }
  .row.recv .body { color: var(--amber); }
  .row.judge .body { font-style: italic; color: var(--text-muted); }
  .row.wait .body,
  .row.llm .body,
  .row.llm-done .body {
    color: var(--text-muted);
  }
  .row.wait .title,
  .row.llm .title {
    font-style: italic;
  }
  .row.evidence .body {
    color: var(--text-muted);
  }
</style>
