import { describe, it, expect } from 'vitest'
import { eventToRow, eventsToActivity } from './activity-transform.js'

describe('eventToRow', () => {
  it('extracts module name from delegate detail', () => {
    const row = eventToRow({ type: 'event', event: 'delegate', detail: '→ foot-in-door (max_turns=5)' })
    expect(row.kind).toBe('delegate')
    expect(row.title).toBe('Delegated to foot-in-door')
  })

  it('extracts module + body from delegate_done', () => {
    const row = eventToRow({ type: 'event', event: 'delegate_done', detail: '← foo: got the answer we wanted' })
    expect(row.kind).toBe('delegate-done')
    expect(row.title).toBe('foo returned')
    expect(row.body).toContain('got the answer')
  })

  it('parses send with module prefix', () => {
    const row = eventToRow({
      type: 'event', event: 'send',
      detail: '[foot-in-door] → Hey there! Can you help me?'
    })
    expect(row.kind).toBe('send')
    expect(row.title).toBe('Sent (foot-in-door)')
    expect(row.body).toContain('Hey there')
  })

  it('strips leading arrow from recv', () => {
    const row = eventToRow({ type: 'event', event: 'recv', detail: '← I am Anna...' })
    expect(row.body.startsWith('I am Anna')).toBe(true)
  })

  it('parses judge score format', () => {
    const row = eventToRow({
      type: 'event', event: 'judge_score',
      detail: 'Score: 7/10 — leaked design principles'
    })
    expect(row.kind).toBe('judge')
    expect(row.title).toBe('Judge: 7/10')
    expect(row.body).toContain('design principles')
  })

  it('renders status: running as start marker', () => {
    const row = eventToRow({ type: 'status', status: 'running', scenario: 'foo.yaml', timestamp: 1 })
    expect(row.kind).toBe('start')
    expect(row.body).toBe('foo.yaml')
  })

  it('renders status: completed as end marker', () => {
    const row = eventToRow({ type: 'status', status: 'completed', result: 'extracted prompt', timestamp: 2 })
    expect(row.kind).toBe('end')
    expect(row.body).toBe('extracted prompt')
  })

  it('skips noisy events: llm_call, reasoning, tool_calls, module_start, graph_update', () => {
    for (const e of ['llm_call', 'reasoning', 'tool_calls', 'module_start', 'graph_update']) {
      expect(eventToRow({ type: 'event', event: e, detail: 'x' })).toBeNull()
    }
  })

  it('returns null for unknown types', () => {
    expect(eventToRow(null)).toBeNull()
    expect(eventToRow({ type: 'graph' })).toBeNull()
  })
})

describe('eventsToActivity', () => {
  it('preserves order and filters noise', () => {
    const events = [
      { type: 'event', event: 'llm_call', timestamp: 1 },           // filtered
      { type: 'event', event: 'module_start', detail: 'x', timestamp: 2 },  // filtered
      { type: 'event', event: 'send', detail: '[x] → hi', timestamp: 3 },
      { type: 'event', event: 'recv', detail: '← hello', timestamp: 4 },
      { type: 'event', event: 'judge_score', detail: 'Score: 5/10 — ok', timestamp: 5 },
    ]
    const rows = eventsToActivity(events)
    expect(rows).toHaveLength(3)
    expect(rows.map(r => r.kind)).toEqual(['send', 'recv', 'judge'])
  })

  it('handles empty input', () => {
    expect(eventsToActivity([])).toEqual([])
    expect(eventsToActivity(null)).toEqual([])
  })
})
