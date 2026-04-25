/**
 * Transform graph + events into a chat message stream for the Co-pilot panel.
 *
 * Chat is the conversation only. Per-attempt outcomes are visible as graph
 * nodes (click to inspect via NodeDetail) — surfacing them here too just
 * floods the conversation. System narrative (run lifecycle, module starts,
 * llm errors, judge verdicts) lives in the Activity column.
 *
 * Chat messages here are:
 *   - human hints      ← legacy graph frontier nodes (source=human) for
 *                        targets that have them on disk; new hints go
 *                        through /api/leader-chat instead.
 *   - agent questions  ← ask_human events
 *   - human answers    ← human_answer events
 *
 * Ordering: by timestamp ascending (oldest first).
 */

/** Build messages from graph nodes (persistent, load-on-boot).
 *  Only legacy human hints surface — explored attempt outcomes do NOT. */
export function messagesFromGraph(graph) {
  if (!graph || !graph.nodes) return []
  const msgs = []

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

/** Distill messages from the transient event stream (this session only).
 *  Only true conversational turns land here — status, errors, lifecycle, and
 *  module_start/conclude/judge events all go to the Activity column. */
export function messagesFromEvents(events) {
  if (!events || events.length === 0) return []
  const msgs = []

  for (const evt of events) {
    if (evt.type !== 'event') continue

    if (evt.event === 'ask_human') {
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
    }
  }

  return msgs
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
