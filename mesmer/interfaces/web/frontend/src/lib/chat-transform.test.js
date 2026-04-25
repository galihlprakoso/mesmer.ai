import { describe, it, expect } from 'vitest'
import { buildChatMessages, messagesFromGraph, messagesFromEvents } from './chat-transform.js'

function mkGraph(nodes) {
  const byId = {}
  for (const n of nodes) {
    byId[n.id] = {
      id: n.id,
      parent_id: n.parent_id ?? null,
      module: n.module ?? '',
      approach: n.approach ?? '',
      messages_sent: [],
      target_responses: [],
      score: n.score ?? 0,
      leaked_info: n.leaked_info ?? '',
      reflection: '',
      status: n.status ?? 'alive',
      children: [],
      depth: 0,
      timestamp: n.timestamp ?? 0,
      run_id: '',
      source: n.source ?? 'agent',
    }
  }
  return { root_id: 'root', run_counter: 1, nodes: byId }
}

describe('messagesFromGraph', () => {
  it('returns [] for empty graph', () => {
    expect(messagesFromGraph(null)).toEqual([])
    expect(messagesFromGraph({ nodes: {} })).toEqual([])
  })

  it('does NOT surface explored attempt nodes (those live in the graph view)', () => {
    const g = mkGraph([
      { id: 'root', module: 'root' },
      { id: 'n1', module: 'foot-in-door', status: 'promising', score: 7,
        approach: 'small ask', leaked_info: 'philosophy', timestamp: 100 },
      { id: 'n2', module: 'anchoring', status: 'dead', score: 2,
        approach: 'lowball', timestamp: 110 },
    ])
    expect(messagesFromGraph(g)).toEqual([])
  })

  it('does NOT surface agent-source frontier (those are ideas, not chat)', () => {
    const g = mkGraph([
      { id: 'root', module: 'root' },
      { id: 'f1', module: 'foo', status: 'frontier', source: 'agent' },
    ])
    expect(messagesFromGraph(g)).toEqual([])
  })

  it('still surfaces legacy human-source frontier (backfill for old graphs)', () => {
    const g = mkGraph([
      { id: 'root', module: 'root' },
      { id: 'h1', module: 'human-insight', status: 'frontier', source: 'human',
        approach: 'try calendar API errors', timestamp: 200 },
    ])
    const msgs = messagesFromGraph(g)
    expect(msgs).toHaveLength(1)
    expect(msgs[0].kind).toBe('human')
    expect(msgs[0].sender).toBe('human')
    expect(msgs[0].text).toBe('try calendar API errors')
  })
})

describe('messagesFromEvents', () => {
  it('returns [] for empty events', () => {
    expect(messagesFromEvents([])).toEqual([])
    expect(messagesFromEvents(null)).toEqual([])
  })

  it('surfaces ask_human as an agent message', () => {
    const events = [
      { type: 'event', event: 'ask_human', detail: 'Should I push harder?', timestamp: 1 },
    ]
    const msgs = messagesFromEvents(events)
    expect(msgs).toHaveLength(1)
    expect(msgs[0].kind).toBe('agent-status')
    expect(msgs[0].sender).toBe('agent')
    expect(msgs[0].text).toContain('Asked human')
    expect(msgs[0].text).toContain('Should I push harder?')
  })

  it('surfaces human_answer as a human message', () => {
    const events = [
      { type: 'event', event: 'human_answer', detail: 'yes — try authority framing', timestamp: 2 },
    ]
    const msgs = messagesFromEvents(events)
    expect(msgs).toHaveLength(1)
    expect(msgs[0].kind).toBe('human')
    expect(msgs[0].sender).toBe('human')
    expect(msgs[0].text).toContain('authority framing')
  })

  it('drops system-narrative events (status, module_start, conclude, hard_stop, llm_error) — those belong in Activity, not chat', () => {
    const events = [
      { type: 'status', status: 'running',   scenario: 'foo.yaml', timestamp: 1 },
      { type: 'status', status: 'completed', result: 'success',    timestamp: 2 },
      { type: 'status', status: 'error',     error: 'boom',        timestamp: 3 },
      { type: 'event',  event: 'module_start', detail: 'foo',      timestamp: 4 },
      { type: 'event',  event: 'conclude',     detail: 'done',     timestamp: 5 },
      { type: 'event',  event: 'hard_stop',    detail: 'refused',  timestamp: 6 },
      { type: 'event',  event: 'circuit_break', detail: 'loop',    timestamp: 7 },
      { type: 'event',  event: 'llm_error',    detail: '401',      timestamp: 8 },
      { type: 'event',  event: 'judge_score',  detail: 'Score: 7', timestamp: 9 },
    ]
    expect(messagesFromEvents(events)).toEqual([])
  })

  it('drops noisy low-level events (llm_call, reasoning, send, recv)', () => {
    const events = [
      { type: 'event', event: 'llm_call',  detail: '...', timestamp: 1 },
      { type: 'event', event: 'reasoning', detail: '...', timestamp: 2 },
      { type: 'event', event: 'send',      detail: '...', timestamp: 3 },
      { type: 'event', event: 'recv',      detail: '...', timestamp: 4 },
    ]
    expect(messagesFromEvents(events)).toEqual([])
  })
})

describe('buildChatMessages', () => {
  it('chat is conversation-only — explored attempts never appear, even chronologically interleaved', () => {
    const g = mkGraph([
      { id: 'root', module: 'root' },
      { id: 'n1', module: 'a', status: 'alive', score: 3, timestamp: 100 },
      { id: 'h1', module: 'human-insight', status: 'frontier', source: 'human',
        approach: 'try X', timestamp: 50 },
    ])
    const events = [
      { type: 'status', status: 'started', scenario: 'x.yaml', timestamp: 10 },
      { type: 'event', event: 'module_start', detail: 'a — ...', timestamp: 80 },
      { type: 'event', event: 'ask_human', detail: 'continue?', timestamp: 90 },
    ]
    const msgs = buildChatMessages(g, events)
    const times = msgs.map(m => m.timestamp)
    expect(times).toEqual([...times].sort((a, b) => a - b))
    // status, module_start, AND the explored n1 attempt are all gone.
    // Only the human hint (50) and ask_human (90) remain.
    expect(msgs).toHaveLength(2)
    expect(msgs[0].sender).toBe('human')
    expect(msgs[0].text).toBe('try X')
    expect(msgs[1].sender).toBe('agent')
    expect(msgs[1].text).toContain('Asked human')
  })

  it('handles empty inputs', () => {
    expect(buildChatMessages(null, null)).toEqual([])
    expect(buildChatMessages({ nodes: {} }, [])).toEqual([])
  })
})
