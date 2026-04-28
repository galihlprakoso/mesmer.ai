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

  it('renders target wait status between send and recv', () => {
    const row = eventToRow({
      type: 'event', event: 'target_wait',
      detail: '[target-profiler] waiting for target response',
    })
    expect(row.kind).toBe('wait')
    expect(row.title).toBe('Waiting for target (target-profiler)')
  })

  it('renders LLM call and completion status rows', () => {
    const call = eventToRow({
      type: 'event', event: 'llm_call',
      detail: '[target-profiler @ depth=1] iteration 2/4 — calling anthropic/claude...',
    })
    expect(call.kind).toBe('llm')
    expect(call.title).toContain('Calling')
    expect(call.body).toContain('iteration 2/4')

    const done = eventToRow({
      type: 'event', event: 'llm_completion',
      detail: JSON.stringify({
        role: 'judge',
        model: 'anthropic/claude',
        elapsed_s: 3.42,
        total_tokens: 1234,
      }),
    })
    expect(done.kind).toBe('llm-done')
    expect(done.title).toBe('Judge LLM returned')
    expect(done.body).toContain('3.4s')
    expect(done.body).toContain('1234 tokens')
  })

  it('renders throttle and retry waits', () => {
    const throttle = eventToRow({ type: 'event', event: 'throttle_wait', detail: 'rpm cap reached; waiting 12.00s' })
    expect(throttle.kind).toBe('wait')
    expect(throttle.title).toBe('Throttled')

    const retry = eventToRow({ type: 'event', event: 'llm_retry', detail: 'retrying in 4s' })
    expect(retry.kind).toBe('warn')
    expect(retry.title).toBe('Retrying LLM call')
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

  it('renders evidence extraction lifecycle rows', () => {
    const start = eventToRow({ type: 'event', event: 'evidence_extract', detail: 'Extracting evidence from target turn 2...' })
    expect(start.kind).toBe('wait')
    expect(start.title).toBe('Extracting evidence')

    const done = eventToRow({ type: 'event', event: 'evidence_extracted', detail: '1 evidence(s) from target turn 2' })
    expect(done.kind).toBe('evidence')
    expect(done.title).toBe('Evidence extracted')
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

  it('renders llm_error as a red error row (the failure mode chat used to swallow)', () => {
    const row = eventToRow({
      type: 'event', event: 'llm_error',
      detail: 'Non-transient: litellm.AuthenticationError: API key not valid',
      timestamp: 1,
    })
    expect(row.kind).toBe('error')
    expect(row.title).toBe('LLM error')
    expect(row.color).toBe('var(--red)')
    expect(row.body).toContain('AuthenticationError')
  })

  it('renders rate_limit_wall as a red wall row', () => {
    const row = eventToRow({ type: 'event', event: 'rate_limit_wall', detail: 'all 3 keys cooled', timestamp: 1 })
    expect(row.kind).toBe('error')
    expect(row.color).toBe('var(--red)')
    expect(row.title).toContain('Rate-limit wall')
  })

  it('renders module_start as a muted start marker with module name', () => {
    const row = eventToRow({
      type: 'event', event: 'module_start',
      detail: 'foot-in-door — tools: send_message, conclude',
      timestamp: 1,
    })
    expect(row.kind).toBe('module-start')
    expect(row.title).toBe('Started foot-in-door')
  })

  it('skips truly noisy events: reasoning, tool_calls, graph_update', () => {
    for (const e of ['reasoning', 'tool_calls', 'graph_update']) {
      expect(eventToRow({ type: 'event', event: e, detail: 'x' })).toBeNull()
    }
  })

  it('returns null for unknown types', () => {
    expect(eventToRow(null)).toBeNull()
    expect(eventToRow({ type: 'graph' })).toBeNull()
  })
})

describe('eventsToActivity', () => {
  it('preserves order and filters only true noise', () => {
    const events = [
      { type: 'event', event: 'llm_call', detail: '[x] iteration 1/1 — calling model...', timestamp: 1 },
      { type: 'event', event: 'module_start', detail: 'x', timestamp: 2 },
      { type: 'event', event: 'send', detail: '[x] → hi', timestamp: 3 },
      { type: 'event', event: 'target_wait', detail: '[x] waiting for target response', timestamp: 3.5 },
      { type: 'event', event: 'recv', detail: '← hello', timestamp: 4 },
      { type: 'event', event: 'judge_score', detail: 'Score: 5/10 — ok', timestamp: 5 },
      { type: 'event', event: 'llm_error', detail: '401 unauthorized', timestamp: 6 },
    ]
    const rows = eventsToActivity(events)
    expect(rows.map(r => r.kind)).toEqual(['llm', 'module-start', 'send', 'wait', 'recv', 'judge', 'error'])
  })

  it('handles empty input', () => {
    expect(eventsToActivity([])).toEqual([])
    expect(eventsToActivity(null)).toEqual([])
  })
})
