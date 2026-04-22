import { describe, it, expect } from 'vitest'
import {
  parseModuleStart,
  nextActiveStack,
  activeStackFromEvents,
} from './module-tracker.js'

describe('parseModuleStart', () => {
  it('parses backend format "${name} — tools: ..."', () => {
    expect(parseModuleStart('foot-in-door \u2014 tools: send_message, conclude'))
      .toBe('foot-in-door')
  })
  it('handles leading/trailing whitespace', () => {
    expect(parseModuleStart('  safety-profiler  \u2014 tools: x'))
      .toBe('safety-profiler')
  })
  it('returns null for empty input', () => {
    expect(parseModuleStart('')).toBe(null)
    expect(parseModuleStart(null)).toBe(null)
  })
  it('falls back to first token if no em-dash', () => {
    expect(parseModuleStart('something')).toBe('something')
  })
})

describe('nextActiveStack', () => {
  it('pushes on module_start', () => {
    const evt = { type: 'event', event: 'module_start', detail: 'foo \u2014 tools: x' }
    expect(nextActiveStack([], evt)).toEqual(['foo'])
    expect(nextActiveStack(['a'], evt)).toEqual(['a', 'foo'])
  })
  it('pops on conclude', () => {
    const evt = { type: 'event', event: 'conclude', detail: 'done' }
    expect(nextActiveStack(['a', 'b'], evt)).toEqual(['a'])
    expect(nextActiveStack(['a'], evt)).toEqual([])
    expect(nextActiveStack([], evt)).toEqual([])  // no-op when empty
  })
  it('pops on hard_stop', () => {
    const evt = { type: 'event', event: 'hard_stop', detail: 'refused' }
    expect(nextActiveStack(['a', 'b'], evt)).toEqual(['a'])
  })
  it('clears on terminal status events', () => {
    for (const status of ['completed', 'error', 'stopped', 'idle']) {
      const evt = { type: 'status', status }
      expect(nextActiveStack(['a', 'b', 'c'], evt)).toEqual([])
    }
  })
  it('ignores unrelated events (send, recv, judge, etc.)', () => {
    const stack = ['leader', 'foo']
    for (const e of ['send', 'recv', 'judge', 'judge_score', 'llm_call', 'reasoning', 'tool_calls']) {
      const evt = { type: 'event', event: e, detail: 'x' }
      expect(nextActiveStack(stack, evt)).toBe(stack)
    }
  })
  it('ignores non-event, non-status messages', () => {
    expect(nextActiveStack(['a'], { type: 'graph' })).toEqual(['a'])
    expect(nextActiveStack(['a'], null)).toEqual(['a'])
  })
})

describe('activeStackFromEvents (realistic sequence)', () => {
  it('simulates leader → delegate → sub → conclude → continue', () => {
    // system-prompt-extraction starts, delegates to safety-profiler which runs
    // and concludes, then delegates to foot-in-door which is still running.
    const events = [
      { type: 'status', status: 'running', scenario: 'x' },
      { type: 'event', event: 'module_start', detail: 'system-prompt-extraction \u2014 tools: ...' },
      { type: 'event', event: 'module_start', detail: 'safety-profiler \u2014 tools: ...' },
      { type: 'event', event: 'send', detail: 'hi' },
      { type: 'event', event: 'recv', detail: 'hello' },
      { type: 'event', event: 'conclude', detail: 'got info' },
      { type: 'event', event: 'module_start', detail: 'foot-in-door \u2014 tools: ...' },
      { type: 'event', event: 'send', detail: 'question' },
    ]
    expect(activeStackFromEvents(events)).toEqual(['system-prompt-extraction', 'foot-in-door'])
  })

  it('full run ends with empty stack', () => {
    const events = [
      { type: 'event', event: 'module_start', detail: 'leader \u2014 tools: x' },
      { type: 'event', event: 'module_start', detail: 'sub \u2014 tools: x' },
      { type: 'event', event: 'conclude', detail: 'sub done' },
      { type: 'event', event: 'conclude', detail: 'leader done' },
      { type: 'status', status: 'completed', result: 'ok' },
    ]
    expect(activeStackFromEvents(events)).toEqual([])
  })

  it('error mid-run clears stack', () => {
    const events = [
      { type: 'event', event: 'module_start', detail: 'a \u2014 tools: x' },
      { type: 'event', event: 'module_start', detail: 'b \u2014 tools: x' },
      { type: 'status', status: 'error', error: 'boom' },
    ]
    expect(activeStackFromEvents(events)).toEqual([])
  })
})
