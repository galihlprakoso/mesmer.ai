/**
 * Transform graph + events into a chat message stream for the Co-pilot panel.
 *
 * Chat messages are derived (not persisted in Phase 1):
 *   - human messages  ← graph frontier nodes with source === "human"
 *   - agent outcomes  ← non-root explored graph nodes (score + status + leaked_info)
 *   - agent status    ← distilled from event stream (module_start / conclude)
 *   - system          ← status events (started / completed / error / stopped)
 *
 * Ordering: by timestamp ascending (oldest first).
 */

/** Distill one chat message from an explored graph node. */
function nodeToAgentMessage(node) {
  const status = node.status
  const icon = status === 'promising' ? '\u2605' : status === 'dead' ? '\u2717' : '\u00b7'
  let text = `${icon} Ran ${node.module}`
  if (node.approach) text += ` — ${node.approach}`
  text += ` (scored ${node.score}/10, ${status})`
  if (node.leaked_info) text += `\nLeaked: ${node.leaked_info}`
  return {
    kind: 'agent-outcome',
    sender: 'agent',
    timestamp: node.timestamp || 0,
    text,
    status,
    score: node.score,
    nodeId: node.id,
  }
}

/** Build messages from graph nodes (persistent, load-on-boot). */
export function messagesFromGraph(graph) {
  if (!graph || !graph.nodes) return []
  const msgs = []

  for (const node of Object.values(graph.nodes)) {
    // Skip root and frontier
    if (node.module === 'root' || node.status === 'frontier') continue
    // Human hints are frontier so they don't land here; agent outcomes only.
    msgs.push(nodeToAgentMessage(node))
  }

  // Human hints (still frontier, but we surface them as sent messages)
  for (const node of Object.values(graph.nodes)) {
    if (node.status === 'frontier' && node.source === 'human') {
      msgs.push({
        kind: 'human',
        sender: 'human',
        timestamp: node.timestamp || 0,
        text: node.approach || '',
        nodeId: node.id,
      })
    }
  }

  msgs.sort((a, b) => a.timestamp - b.timestamp)
  return msgs
}

/** Distill messages from the transient event stream (this session only). */
export function messagesFromEvents(events) {
  if (!events || events.length === 0) return []
  const msgs = []

  for (const evt of events) {
    if (evt.type === 'status') {
      msgs.push({
        kind: 'system',
        sender: 'system',
        timestamp: evt.timestamp || 0,
        text: statusText(evt),
      })
      continue
    }

    if (evt.type !== 'event') continue

    // Module-level narrative events only. Leave the noisy ones for Trace.
    if (evt.event === 'module_start') {
      msgs.push({
        kind: 'agent-status',
        sender: 'agent',
        timestamp: evt.timestamp || 0,
        text: `Started: ${shortDetail(evt.detail)}`,
      })
    } else if (evt.event === 'conclude') {
      msgs.push({
        kind: 'agent-status',
        sender: 'agent',
        timestamp: evt.timestamp || 0,
        text: `Concluded: ${shortDetail(evt.detail)}`,
      })
    } else if (evt.event === 'ask_human') {
      msgs.push({
        kind: 'agent-status',
        sender: 'agent',
        timestamp: evt.timestamp || 0,
        text: `Asked human: ${shortDetail(evt.detail)}`,
      })
    } else if (evt.event === 'human_answer') {
      msgs.push({
        kind: 'human',
        sender: 'human',
        timestamp: evt.timestamp || 0,
        text: shortDetail(evt.detail),
      })
    } else if (evt.event === 'hard_stop' || evt.event === 'circuit_break') {
      msgs.push({
        kind: 'system',
        sender: 'system',
        timestamp: evt.timestamp || 0,
        text: shortDetail(evt.detail),
      })
    }
  }

  return msgs
}

function statusText(evt) {
  switch (evt.status) {
    case 'running':   return `Attack running: ${evt.scenario || ''}`
    case 'started':   return `Attack started: ${evt.scenario || ''}`  // legacy alias
    case 'completed': return `Attack completed — ${evt.result || ''}`
    case 'stopped':   return 'Attack stopped'
    case 'error':     return `Error: ${evt.error || 'unknown'}`
    default:          return evt.status || ''
  }
}

function shortDetail(detail) {
  if (!detail) return ''
  return detail.length > 200 ? detail.slice(0, 200) + '...' : detail
}

/**
 * Merge graph-derived and event-derived messages into a single chronological stream.
 * Deduplicates trivially by (kind, text, timestamp) to avoid showing an outcome
 * twice when the graph is re-broadcast.
 */
export function buildChatMessages(graph, events) {
  const a = messagesFromGraph(graph)
  const b = messagesFromEvents(events)

  const merged = [...a, ...b]
  merged.sort((x, y) => x.timestamp - y.timestamp)

  const seen = new Set()
  const out = []
  for (const m of merged) {
    const key = `${m.kind}|${m.text}|${m.timestamp}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(m)
  }
  return out
}
