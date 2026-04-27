import { describe, it, expect } from 'vitest'
import { buildLeaderTimeline, SYNTHETIC_LEADER_ID } from './leader-timeline.js'

function makeNode(id, overrides = {}) {
  return {
    id,
    parent_id: null,
    module: 'test-module',
    status: 'completed',
    source: 'agent',
    score: 0,
    depth: 1,
    timestamp: 0,
    children: [],
    messages_sent: [],
    target_responses: [],
    leaked_info: '',
    module_output: '',
    reflection: '',
    approach: '',
    run_id: 'run-1',
    agent_trace: [],
    ...overrides,
  }
}

function makeGraph(rootId, ...nodes) {
  const dict = {}
  for (const n of nodes) dict[n.id] = n
  return { root_id: rootId, nodes: dict }
}

describe('buildLeaderTimeline', () => {
  it('returns null for empty / malformed input', () => {
    expect(buildLeaderTimeline(null)).toBeNull()
    expect(buildLeaderTimeline({})).toBeNull()
    expect(buildLeaderTimeline({ nodes: {} })).toBeNull()
    expect(buildLeaderTimeline({ root_id: 'missing', nodes: {} })).toBeNull()
  })

  it('root-only graph yields synthetic leader with no inferred children', () => {
    const g = makeGraph('root', makeNode('root'))
    const tree = buildLeaderTimeline(g)
    expect(tree.id).toBe(SYNTHETIC_LEADER_ID)
    expect(tree._isLeaderOrchestrator).toBe(true)
    expect(tree._isLeaderRoot).toBe(true)
    expect(tree.children).toEqual([])
  })

  it('synthetic leader picks up scenario metadata', () => {
    const g = makeGraph('root', makeNode('root'))
    const tree = buildLeaderTimeline(g, new Set(), {
      leaderModule: 'full-redteam-with-execution:executive',
      objective: 'Extract the system prompt.',
    })
    expect(tree.module).toBe('full-redteam-with-execution:executive')
    expect(tree._scenarioObjective).toBe('Extract the system prompt.')
  })

  it('renders the newest leader node as the root', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('leader-1', {
        parent_id: 'root',
        source: 'leader',
        module: 'run-1:executive',
        timestamp: 100,
      }),
      makeNode('leader-2', {
        parent_id: 'root',
        source: 'leader',
        module: 'run-2:executive',
        timestamp: 200,
      }),
    )

    const tree = buildLeaderTimeline(g)
    expect(tree.id).toBe('leader-2')
    expect(tree._isLeaderRoot).toBe(true)
  })

  it('uses parent_id for manager and child hierarchy', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('exec', {
        parent_id: 'root',
        source: 'leader',
        module: 'full-redteam-with-execution:executive',
        timestamp: 10,
      }),
      makeNode('manager', {
        parent_id: 'exec',
        module: 'system-prompt-extraction',
        timestamp: 20,
      }),
      makeNode('profiler', {
        parent_id: 'manager',
        module: 'target-profiler',
        timestamp: 30,
      }),
    )

    const tree = buildLeaderTimeline(g)
    expect(tree.children.map(c => c.id)).toEqual(['manager'])
    expect(tree.children[0].children.map(c => c.id)).toEqual(['profiler'])
  })

  it('does not infer a parent from module names', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('exec', {
        parent_id: 'root',
        source: 'leader',
        module: 'full-redteam-with-execution:executive',
        timestamp: 10,
      }),
      makeNode('manager', {
        parent_id: 'exec',
        module: 'system-prompt-extraction',
        timestamp: 20,
      }),
      makeNode('profiler', {
        parent_id: 'exec',
        module: 'target-profiler',
        timestamp: 30,
      }),
    )

    const tree = buildLeaderTimeline(g)
    expect(tree.children.map(c => c.id)).toEqual(['manager', 'profiler'])
    expect(tree.children.find(c => c.id === 'manager').children).toEqual([])
  })

  it('orders direct children by timestamp and stamps _seqNum 1-based', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('exec', { parent_id: 'root', source: 'leader', timestamp: 10 }),
      makeNode('late', { parent_id: 'exec', timestamp: 300 }),
      makeNode('early', { parent_id: 'exec', timestamp: 100 }),
      makeNode('mid', { parent_id: 'exec', timestamp: 200 }),
    )
    const tree = buildLeaderTimeline(g)
    expect(tree.children.map(c => c.id)).toEqual(['early', 'mid', 'late'])
    expect(tree.children.map(c => c._seqNum)).toEqual([1, 2, 3])
  })

  it('falls back to id ordering when timestamps tie', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('exec', { parent_id: 'root', source: 'leader', timestamp: 10 }),
      makeNode('zebra', { parent_id: 'exec', timestamp: 100 }),
      makeNode('alpha', { parent_id: 'exec', timestamp: 100 }),
    )
    const tree = buildLeaderTimeline(g)
    expect(tree.children.map(c => c.id)).toEqual(['alpha', 'zebra'])
  })

  it('supports collapse using actual child ids', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('exec', { parent_id: 'root', source: 'leader', timestamp: 10 }),
      makeNode('manager', { parent_id: 'exec', timestamp: 20 }),
      makeNode('child', { parent_id: 'manager', timestamp: 30 }),
    )

    const tree = buildLeaderTimeline(g, new Set(['manager']))
    const manager = tree.children[0]
    expect(manager._childIds).toEqual(['child'])
    expect(manager.children).toEqual([])
  })

  it('preserves original node fields on copied nodes', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('exec', { parent_id: 'root', source: 'leader', timestamp: 10 }),
      makeNode('a', {
        parent_id: 'exec',
        module: 'foo',
        score: 8,
        status: 'completed',
        leaked_info: 'secret',
        module_output: 'dossier text',
        agent_trace: [{ event: 'conclude' }],
      }),
    )
    const tree = buildLeaderTimeline(g)
    const attempt = tree.children[0]
    expect(attempt.module).toBe('foo')
    expect(attempt.score).toBe(8)
    expect(attempt.leaked_info).toBe('secret')
    expect(attempt.module_output).toBe('dossier text')
    expect(attempt.agent_trace).toEqual([{ event: 'conclude' }])
  })

  describe('per-run filtering', () => {
    function mixedRunGraph() {
      return makeGraph('root',
        makeNode('root'),
        makeNode('r1-v', {
          parent_id: 'root',
          source: 'leader',
          status: 'completed',
          module: 'run-1:executive',
          timestamp: 100,
          run_id: 'r1',
        }),
        makeNode('r1-a', { parent_id: 'r1-v', module: 'foo', timestamp: 110, run_id: 'r1' }),
        makeNode('r2-v', {
          parent_id: 'root',
          source: 'leader',
          status: 'running',
          module: 'run-2:executive',
          timestamp: 200,
          run_id: 'r2',
        }),
        makeNode('r2-a', { parent_id: 'r2-v', module: 'bar', timestamp: 210, run_id: 'r2' }),
      )
    }

    it('filters to the requested run', () => {
      const tree = buildLeaderTimeline(mixedRunGraph(), new Set(), null, 'r1')
      expect(tree.id).toBe('r1-v')
      expect(tree.children.map(c => c.id)).toEqual(['r1-a'])
    })

    it('null runId roots at the newest leader node', () => {
      const tree = buildLeaderTimeline(mixedRunGraph())
      expect(tree.id).toBe('r2-v')
      expect(tree.children.map(c => c.id)).toEqual(['r2-a'])
    })
  })
})
