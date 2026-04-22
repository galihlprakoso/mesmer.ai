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

  it('skips root node', () => {
    const g = mkGraph([{ id: 'root', module: 'root', status: 'alive' }])
    expect(messagesFromGraph(g)).toEqual([])
  })

  it('skips agent-generated frontier (those are ideas, not messages)', () => {
    const g = mkGraph([
      { id: 'root', module: 'root' },
      { id: 'f1', module: 'foo', status: 'frontier', source: 'agent' },
    ])
    expect(messagesFromGraph(g)).toEqual([])
  })

  it('turns explored nodes into agent-outcome messages', () => {
    const g = mkGraph([
      { id: 'root', module: 'root' },
      { id: 'n1', module: 'foot-in-door', status: 'promising', score: 7,
        approach: 'small ask', leaked_info: 'philosophy', timestamp: 100 },
    ])
    const msgs = messagesFromGraph(g)
    expect(msgs).toHaveLength(1)
    expect(msgs[0].kind).toBe('agent-outcome')
    expect(msgs[0].sender).toBe('agent')
    expect(msgs[0].score).toBe(7)
    expect(msgs[0].status).toBe('promising')
    expect(msgs[0].text).toContain('foot-in-door')
    expect(msgs[0].text).toContain('small ask')
    expect(msgs[0].text).toContain('7/10')
    expect(msgs[0].text).toContain('philosophy')
  })

  it('turns human-source frontier into human messages', () => {
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

  it('sorts by timestamp ascending', () => {
    const g = mkGraph([
      { id: 'root', module: 'root' },
      { id: 'n1', module: 'a', status: 'alive', score: 3, timestamp: 300 },
      { id: 'n2', module: 'b', status: 'alive', score: 4, timestamp: 100 },
      { id: 'h1', module: 'human-insight', status: 'frontier', source: 'human', timestamp: 200 },
    ])
    const msgs = messagesFromGraph(g)
    expect(msgs.map(m => m.timestamp)).toEqual([100, 200, 300])
  })
})

describe('messagesFromEvents', () => {
  it('returns [] for empty events', () => {
    expect(messagesFromEvents([])).toEqual([])
    expect(messagesFromEvents(null)).toEqual([])
  })

  it('surfaces module_start / conclude events as agent status', () => {
    const events = [
      { type: 'event', event: 'module_start', detail: 'foot-in-door — tools: x,y', timestamp: 1 },
      { type: 'event', event: 'llm_call',     detail: 'calling ...', timestamp: 2 },
      { type: 'event', event: 'conclude',     detail: 'Got the system prompt!', timestamp: 3 },
    ]
    const msgs = messagesFromEvents(events)
    expect(msgs).toHaveLength(2)
    expect(msgs[0].kind).toBe('agent-status')
    expect(msgs[0].text).toContain('Started')
    expect(msgs[1].text).toContain('Concluded')
  })

  it('surfaces hard_stop / circuit_break as system messages', () => {
    const events = [
      { type: 'event', event: 'hard_stop', detail: 'Model refused', timestamp: 1 },
    ]
    const msgs = messagesFromEvents(events)
    expect(msgs).toHaveLength(1)
    expect(msgs[0].kind).toBe('system')
  })

  it('renders status events', () => {
    const events = [
      { type: 'status', status: 'started', scenario: 'foo.yaml', timestamp: 1 },
      { type: 'status', status: 'completed', result: 'success', timestamp: 2 },
    ]
    const msgs = messagesFromEvents(events)
    expect(msgs).toHaveLength(2)
    expect(msgs[0].sender).toBe('system')
    expect(msgs[0].text).toContain('started')
    expect(msgs[1].text).toContain('completed')
  })

  it('ignores noisy events (llm_call, reasoning, send, recv, judge_score)', () => {
    const events = [
      { type: 'event', event: 'llm_call', detail: '...', timestamp: 1 },
      { type: 'event', event: 'reasoning', detail: '...', timestamp: 2 },
      { type: 'event', event: 'send', detail: '...', timestamp: 3 },
      { type: 'event', event: 'recv', detail: '...', timestamp: 4 },
      { type: 'event', event: 'judge_score', detail: '...', timestamp: 5 },
    ]
    expect(messagesFromEvents(events)).toEqual([])
  })
})

describe('buildChatMessages', () => {
  it('merges graph + events in timestamp order', () => {
    const g = mkGraph([
      { id: 'root', module: 'root' },
      { id: 'n1', module: 'a', status: 'alive', score: 3, timestamp: 100 },
      { id: 'h1', module: 'human-insight', status: 'frontier', source: 'human',
        approach: 'try X', timestamp: 50 },
    ])
    const events = [
      { type: 'status', status: 'started', scenario: 'x.yaml', timestamp: 10 },
      { type: 'event', event: 'module_start', detail: 'a — ...', timestamp: 80 },
    ]
    const msgs = buildChatMessages(g, events)
    const times = msgs.map(m => m.timestamp)
    expect(times).toEqual([...times].sort((a, b) => a - b))
    expect(msgs[0].sender).toBe('system')      // started
    expect(msgs[1].sender).toBe('human')        // hint at 50
    expect(msgs[2].sender).toBe('agent')        // module_start at 80
    expect(msgs[3].sender).toBe('agent')        // outcome at 100
  })

  it('deduplicates identical messages', () => {
    const g = mkGraph([
      { id: 'root', module: 'root' },
      { id: 'n1', module: 'a', status: 'alive', score: 3, timestamp: 100 },
    ])
    // Call twice with same data (simulating graph re-broadcast)
    const once = buildChatMessages(g, [])
    const twice = buildChatMessages(g, [])
    expect(once.length).toBe(twice.length)
  })
})
