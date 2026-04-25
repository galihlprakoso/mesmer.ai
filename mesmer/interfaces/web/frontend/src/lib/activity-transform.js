/**
 * Transform the raw events stream into narrative Activity rows for
 * the right-sidebar ActivityPanel.
 *
 * Philosophy: the Trace view shows everything raw. ActivityPanel shows
 * only the human-meaningful beats — what the agent sent, what came back,
 * judge scores, delegations, conclusions.
 *
 * Output entry shape:
 *   { kind, time, icon, color, title, body? }
 */

function shorten(s, n) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n).trim() + '…' : s
}

/**
 * Map a single event to an activity row (or null to skip).
 * Detail strings follow the log formats in loop.py.
 */
export function eventToRow(evt) {
  if (!evt) return null

  if (evt.type === 'status') {
    switch (evt.status) {
      case 'running':
        return { kind: 'start', time: evt.timestamp, icon: '▶', color: 'var(--green)',
                 title: 'Attack started', body: evt.scenario || '' }
      case 'completed':
        return { kind: 'end', time: evt.timestamp, icon: '✓', color: 'var(--green)',
                 title: 'Attack completed', body: evt.result || '' }
      case 'error':
        return { kind: 'error', time: evt.timestamp, icon: '✗', color: 'var(--red)',
                 title: 'Error', body: evt.error || '' }
      case 'stopped':
        return { kind: 'end', time: evt.timestamp, icon: '◼', color: 'var(--red)',
                 title: 'Attack stopped' }
      case 'human_question':
        return { kind: 'ask', time: evt.timestamp, icon: '?', color: 'var(--accent)',
                 title: 'Agent asked you', body: evt.question || '' }
      default:
        return null
    }
  }

  if (evt.type !== 'event') return null

  const d = evt.detail || ''
  switch (evt.event) {
    case 'delegate': {
      // "→ foo (max_turns=N)"
      const match = d.match(/→\s*([^\s(]+)/)
      const name = match ? match[1] : shorten(d, 80)
      return { kind: 'delegate', time: evt.timestamp, icon: '↳', color: 'var(--accent)',
               title: `Delegated to ${name}` }
    }
    case 'delegate_done': {
      const match = d.match(/←\s*([^\s:]+):\s*(.*)/)
      if (match) {
        return { kind: 'delegate-done', time: evt.timestamp, icon: '↲', color: 'var(--accent)',
                 title: `${match[1]} returned`, body: shorten(match[2], 200) }
      }
      return { kind: 'delegate-done', time: evt.timestamp, icon: '↲', color: 'var(--accent)',
               title: 'Sub-module returned', body: shorten(d, 200) }
    }
    case 'send': {
      // "[module] → message..."
      const m = d.match(/^\[([^\]]+)\]\s*→\s*([\s\S]*)$/)
      return {
        kind: 'send', time: evt.timestamp, icon: '→', color: 'var(--cyan)',
        title: m ? `Sent (${m[1]})` : 'Sent',
        body: shorten(m ? m[2] : d, 400),
      }
    }
    case 'recv':
      return {
        kind: 'recv', time: evt.timestamp, icon: '←', color: 'var(--amber)',
        title: 'Received',
        body: shorten(d.replace(/^←\s*/, ''), 400),
      }
    case 'judge_score': {
      // "Score: N/10 — leaked info"
      const m = d.match(/Score:\s*(\d+)\/10(?:\s*—\s*(.*))?/)
      if (m) {
        return { kind: 'judge', time: evt.timestamp, icon: '⚖', color: 'var(--blue)',
                 title: `Judge: ${m[1]}/10`, body: m[2] || '' }
      }
      return { kind: 'judge', time: evt.timestamp, icon: '⚖', color: 'var(--blue)',
               title: 'Judge', body: shorten(d, 200) }
    }
    case 'conclude':
      return { kind: 'conclude', time: evt.timestamp, icon: '✓', color: 'var(--green)',
               title: 'Concluded', body: shorten(d, 300) }
    case 'frontier':
      return { kind: 'frontier', time: evt.timestamp, icon: '🌱', color: 'var(--blue)',
               title: 'New idea', body: shorten(d.replace(/^🌿\s*New frontier:\s*/u, ''), 200) }
    case 'ask_human':
      return { kind: 'ask', time: evt.timestamp, icon: '?', color: 'var(--accent)',
               title: 'Agent asked you', body: shorten(d.replace(/^\?\s*/, ''), 300) }
    case 'human_answer':
      return { kind: 'answer', time: evt.timestamp, icon: '!', color: 'var(--amber)',
               title: 'You answered', body: shorten(d.replace(/^!\s*/, ''), 300) }
    case 'budget':
      return { kind: 'warn', time: evt.timestamp, icon: '⚠', color: 'var(--red)',
               title: 'Turn budget exhausted' }
    case 'hard_stop':
      return { kind: 'warn', time: evt.timestamp, icon: '⛔', color: 'var(--red)',
               title: 'Hard stop', body: shorten(d, 200) }
    case 'send_error':
      return { kind: 'error', time: evt.timestamp, icon: '✗', color: 'var(--red)',
               title: 'Send failed', body: shorten(d, 200) }
    case 'llm_error':
      return { kind: 'error', time: evt.timestamp, icon: '✗', color: 'var(--red)',
               title: 'LLM error', body: shorten(d, 600) }
    case 'rate_limit_wall':
      return { kind: 'error', time: evt.timestamp, icon: '⛔', color: 'var(--red)',
               title: 'Rate-limit wall — all keys cooled', body: shorten(d, 200) }
    case 'module_start': {
      const m = d.match(/^(\S+)/)
      return { kind: 'module-start', time: evt.timestamp, icon: '▸', color: 'var(--text-muted)',
               title: m ? `Started ${m[1]}` : 'Module started' }
    }
    // Silent: llm_call, reasoning, tool_calls, graph_update
    default:
      return null
  }
}

export function eventsToActivity(events) {
  if (!events) return []
  const rows = []
  for (const evt of events) {
    const row = eventToRow(evt)
    if (row) rows.push(row)
  }
  return rows
}
