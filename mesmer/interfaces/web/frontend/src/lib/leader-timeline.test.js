import { describe, it, expect } from 'vitest'
import { buildLeaderTimeline, SYNTHETIC_LEADER_ID } from './leader-timeline.js'

function makeNode(id, overrides = {}) {
  return {
    id,
    parent_id: null,
    module: 'test-module',
    status: 'alive',
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

  it('root-only graph yields synthetic leader at the root with no children', () => {
    const g = makeGraph('root', makeNode('root'))
    const tree = buildLeaderTimeline(g)
    expect(tree.id).toBe(SYNTHETIC_LEADER_ID)
    expect(tree._isLeaderOrchestrator).toBe(true)
    expect(tree._isLeaderRoot).toBe(true)
    expect(tree.children).toEqual([])
  })

  it('synthetic leader picks up scenarioMeta.leaderModule', () => {
    const g = makeGraph('root', makeNode('root'))
    const tree = buildLeaderTimeline(g, new Set(), {
      leaderModule: 'system-prompt-extraction',
      objective: 'Extract the system prompt.',
    })
    expect(tree.module).toBe('system-prompt-extraction')
    expect(tree._scenarioObjective).toBe('Extract the system prompt.')
  })

  it('reparents non-frontier non-verdict attempts under the leader root', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('a', { parent_id: 'root', module: 'foo', score: 5, timestamp: 100 }),
      makeNode('b', { parent_id: 'root', module: 'bar', score: 7, timestamp: 200 }),
    )
    const tree = buildLeaderTimeline(g)
    expect(tree.children).toHaveLength(2)
    expect(tree.children.map(c => c.module)).toEqual(['foo', 'bar'])
  })

  it('nests known manager sub-module attempts under the manager attempt', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('profiler', {
        parent_id: 'root',
        module: 'target-profiler',
        timestamp: 100,
      }),
      makeNode('planner', {
        parent_id: 'root',
        module: 'attack-planner',
        timestamp: 110,
      }),
      makeNode('manager', {
        parent_id: 'root',
        module: 'system-prompt-extraction',
        timestamp: 120,
      }),
      makeNode('analysis', {
        parent_id: 'root',
        module: 'exploit-analysis',
        timestamp: 200,
      }),
    )

    const tree = buildLeaderTimeline(g)

    expect(tree.children.map(c => c.module)).toEqual([
      'system-prompt-extraction',
      'exploit-analysis',
    ])
    expect(tree.children[0].children.map(c => c.module)).toEqual([
      'target-profiler',
      'attack-planner',
    ])
  })

  it('synthesizes a missing fixed-order manager while its child attempts are live', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('recital', {
        parent_id: 'root',
        module: 'instruction-recital',
        timestamp: 100,
      }),
      makeNode('planner', {
        parent_id: 'root',
        module: 'attack-planner',
        timestamp: 110,
      }),
    )

    const tree = buildLeaderTimeline(
      g,
      new Set(),
      {
        leaderModule: 'full-redteam-with-execution:executive',
        modules: ['system-prompt-extraction', 'exploit-analysis', 'exploit-executor'],
      },
      null,
      'system-prompt-extraction',
    )

    expect(tree.children).toHaveLength(1)
    expect(tree.children[0].id).toBe('__manager__system-prompt-extraction')
    expect(tree.children[0].module).toBe('system-prompt-extraction')
    expect(tree.children[0]._isSyntheticManager).toBe(true)
    expect(tree.children[0].children.map(c => c.module)).toEqual([
      'instruction-recital',
      'attack-planner',
    ])
  })

  it('does not synthesize missing managers when no manager is active', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('recital', {
        parent_id: 'root',
        module: 'instruction-recital',
        timestamp: 100,
      }),
      makeNode('planner', {
        parent_id: 'root',
        module: 'attack-planner',
        timestamp: 110,
      }),
    )

    const tree = buildLeaderTimeline(
      g,
      new Set(),
      {
        leaderModule: 'full-redteam-with-execution:executive',
        modules: ['system-prompt-extraction', 'exploit-analysis', 'exploit-executor'],
      },
    )

    expect(tree.children.map(c => c.module)).toEqual([
      'instruction-recital',
      'attack-planner',
    ])
    expect(tree.children.every(c => c.children.length === 0)).toBe(true)
  })

  it('post-run does not show a ghost synthetic executor manager', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('analysis', {
        parent_id: 'root',
        module: 'exploit-analysis',
        timestamp: 100,
      }),
      makeNode('probe', {
        parent_id: 'root',
        module: 'foot-in-door',
        timestamp: 90,
      }),
      makeNode('verdict', {
        parent_id: 'analysis',
        source: 'leader',
        status: 'dead',
        module: 'full-redteam-with-execution:executive',
        timestamp: 120,
      }),
    )

    const tree = buildLeaderTimeline(
      g,
      new Set(),
      {
        leaderModule: 'full-redteam-with-execution:executive',
        modules: ['system-prompt-extraction', 'exploit-analysis', 'exploit-executor'],
      },
    )

    expect(tree.children.map(c => c.id)).not.toContain('__manager__exploit-executor')
    expect(tree.children.map(c => c.module)).toEqual(['exploit-analysis'])
  })

  it('hides ordered-manager frontier proposals under ordered-manager attempts', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('spe', {
        parent_id: 'root',
        module: 'system-prompt-extraction',
        timestamp: 100,
        children: ['self-frontier', 'next-frontier', 'tech-frontier'],
      }),
      makeNode('self-frontier', {
        parent_id: 'spe',
        module: 'system-prompt-extraction',
        status: 'frontier',
      }),
      makeNode('next-frontier', {
        parent_id: 'spe',
        module: 'exploit-analysis',
        status: 'frontier',
      }),
      makeNode('tech-frontier', {
        parent_id: 'spe',
        module: 'direct-ask',
        status: 'frontier',
      }),
    )

    const tree = buildLeaderTimeline(
      g,
      new Set(),
      {
        leaderModule: 'full-redteam-with-execution:executive',
        modules: ['system-prompt-extraction', 'exploit-analysis', 'exploit-executor'],
      },
    )

    const manager = tree.children[0]
    expect(manager.children.map(c => c.id)).toEqual(['tech-frontier'])
  })

  it('orders children by timestamp and stamps _seqNum 1-based', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('late', { parent_id: 'root', timestamp: 300 }),
      makeNode('early', { parent_id: 'root', timestamp: 100 }),
      makeNode('mid', { parent_id: 'root', timestamp: 200 }),
    )
    const tree = buildLeaderTimeline(g)
    const ids = tree.children.map(c => c.id)
    const seqs = tree.children.map(c => c._seqNum)
    expect(ids).toEqual(['early', 'mid', 'late'])
    expect(seqs).toEqual([1, 2, 3])
  })

  it('falls back to id ordering when timestamps tie', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('zebra', { parent_id: 'root', timestamp: 100 }),
      makeNode('alpha', { parent_id: 'root', timestamp: 100 }),
    )
    const tree = buildLeaderTimeline(g)
    expect(tree.children.map(c => c.id)).toEqual(['alpha', 'zebra'])
  })

  it('attaches frontier proposals to their parent attempt as leaves', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('attempt', { parent_id: 'root', timestamp: 100, children: ['front'] }),
      makeNode('front', { parent_id: 'attempt', status: 'frontier', module: 'next-tech' }),
    )
    const tree = buildLeaderTimeline(g)
    const attempt = tree.children[0]
    expect(attempt.id).toBe('attempt')
    expect(attempt.children).toHaveLength(1)
    expect(attempt.children[0].id).toBe('front')
    expect(attempt.children[0].status).toBe('frontier')
    expect(attempt.children[0].children).toEqual([])
  })

  it('frontier proposals do not appear as direct children of the leader root', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('attempt', { parent_id: 'root', timestamp: 100 }),
      makeNode('front', { parent_id: 'root', status: 'frontier' }),
    )
    const tree = buildLeaderTimeline(g)
    // Only the real attempt is a direct child; the orphan-frontier under
    // root is not surfaced (no surviving parent in the rendered tree).
    expect(tree.children).toHaveLength(1)
    expect(tree.children[0].id).toBe('attempt')
  })

  it('post-run: leader-verdict node IS the root, with attempts as children', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('a', { parent_id: 'root', timestamp: 100 }),
      makeNode('b', { parent_id: 'root', timestamp: 200 }),
      makeNode('verdict', {
        parent_id: 'b', source: 'leader', status: 'promising',
        module: 'system-prompt-extraction', module_output: 'Won.', timestamp: 50,
      }),
    )
    const tree = buildLeaderTimeline(g)
    expect(tree.id).toBe('verdict')
    expect(tree.source).toBe('leader')
    expect(tree.status).toBe('promising')
    expect(tree.module).toBe('system-prompt-extraction')
    expect(tree._isLeaderRoot).toBe(true)
    expect(tree.children).toHaveLength(2)
    expect(tree.children.map(c => c.id)).toEqual(['a', 'b'])
  })

  it('mid-run: no verdict yet — synthetic stub root, attempts as children', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('a', { parent_id: 'root', timestamp: 100 }),
      makeNode('b', { parent_id: 'root', timestamp: 200 }),
    )
    const tree = buildLeaderTimeline(g)
    expect(tree._isLeaderOrchestrator).toBe(true)
    expect(tree.children).toHaveLength(2)
    expect(tree.children.every(c => c.source !== 'leader')).toBe(true)
  })

  it('collapsed set hides a node\'s frontier children but keeps the node', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('attempt', { parent_id: 'root', timestamp: 100, children: ['front'] }),
      makeNode('front', { parent_id: 'attempt', status: 'frontier' }),
    )
    const collapsed = new Set(['attempt'])
    const tree = buildLeaderTimeline(g, collapsed)
    const attempt = tree.children[0]
    expect(attempt.id).toBe('attempt')
    expect(attempt.children).toEqual([])
  })

  it('preserves all original node fields on copied attempts', () => {
    const g = makeGraph('root',
      makeNode('root'),
      makeNode('a', {
        parent_id: 'root', module: 'foo', score: 8, status: 'promising',
        leaked_info: 'secret', module_output: 'dossier text',
      }),
    )
    const tree = buildLeaderTimeline(g)
    const attempt = tree.children[0]
    expect(attempt.module).toBe('foo')
    expect(attempt.score).toBe(8)
    expect(attempt.status).toBe('promising')
    expect(attempt.leaked_info).toBe('secret')
    expect(attempt.module_output).toBe('dossier text')
  })

  describe('per-run filtering (runId arg)', () => {
    function mixedRunGraph() {
      // Two runs: r1 (concluded with promising verdict), r2 (concluded with dead verdict).
      return makeGraph('root',
        makeNode('root'),
        // r1 attempts
        makeNode('r1-a', { parent_id: 'root', module: 'foo', timestamp: 100, run_id: 'r1', score: 7 }),
        makeNode('r1-b', { parent_id: 'root', module: 'bar', timestamp: 110, run_id: 'r1', score: 5 }),
        // r1 verdict (promising = objective met)
        makeNode('r1-v', {
          parent_id: 'r1-b', source: 'leader', status: 'promising',
          module: 'system-prompt-extraction', timestamp: 120, run_id: 'r1',
        }),
        // r1 frontier suggestion
        makeNode('r1-f', { parent_id: 'r1-a', status: 'frontier', module: 'qux', run_id: 'r1' }),
        // r2 attempts
        makeNode('r2-a', { parent_id: 'root', module: 'baz', timestamp: 200, run_id: 'r2', score: 3 }),
        // r2 verdict (dead = no consolidation)
        makeNode('r2-v', {
          parent_id: 'r2-a', source: 'leader', status: 'dead',
          module: 'system-prompt-extraction', timestamp: 220, run_id: 'r2',
        }),
        // r2 frontier
        makeNode('r2-f', { parent_id: 'r2-a', status: 'frontier', module: 'qux2', run_id: 'r2' }),
      )
    }

    it('filters attempts to a single run when runId is set', () => {
      const tree = buildLeaderTimeline(mixedRunGraph(), new Set(), null, 'r1')
      // r1 verdict becomes the root (post-run case).
      expect(tree.id).toBe('r1-v')
      expect(tree.run_id).toBe('r1')
      // Only r1's attempts hang underneath; r2's are filtered out.
      const ids = tree.children.map(c => c.id)
      expect(ids.sort()).toEqual(['r1-a', 'r1-b'])
    })

    it('keeps frontier proposals that belong to the selected run, drops others', () => {
      const tree = buildLeaderTimeline(mixedRunGraph(), new Set(), null, 'r1')
      // r1-f hangs off r1-a; r2-f must NOT appear anywhere.
      const r1a = tree.children.find(c => c.id === 'r1-a')
      expect(r1a.children.map(c => c.id)).toEqual(['r1-f'])
      // Walk the whole tree to make sure r2-f isn't lurking.
      function walk(node) {
        const seen = [node.id]
        for (const c of node.children || []) seen.push(...walk(c))
        return seen
      }
      expect(walk(tree)).not.toContain('r2-f')
    })

    it('picks the right leader-verdict for the requested run', () => {
      const t1 = buildLeaderTimeline(mixedRunGraph(), new Set(), null, 'r1')
      expect(t1.id).toBe('r1-v')
      expect(t1.status).toBe('promising')

      const t2 = buildLeaderTimeline(mixedRunGraph(), new Set(), null, 'r2')
      expect(t2.id).toBe('r2-v')
      expect(t2.status).toBe('dead')
    })

    it('null runId keeps all-runs behaviour: every attempt + most-recent verdict', () => {
      const tree = buildLeaderTimeline(mixedRunGraph())
      // Most-recent verdict (r2-v at ts=220) wins.
      expect(tree.id).toBe('r2-v')
      // All four explored attempts from both runs are included.
      const ids = tree.children
        .filter(c => c.source !== 'leader')
        .map(c => c.id)
        .sort()
      expect(ids).toEqual(['r1-a', 'r1-b', 'r2-a'])
    })

    it('synthesises a stub when the requested run has no verdict yet', () => {
      // Same graph but strip r2-v to simulate r2 mid-execution.
      const g = mixedRunGraph()
      delete g.nodes['r2-v']
      const tree = buildLeaderTimeline(g, new Set(), { leaderModule: 'sys' }, 'r2')
      expect(tree._isLeaderOrchestrator).toBe(true)
      expect(tree.module).toBe('sys')
      expect(tree.children.map(c => c.id)).toEqual(['r2-a'])
    })
  })
})
