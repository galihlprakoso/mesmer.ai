import { describe, it, expect } from 'vitest'
import { buildTree, groupSiblings, bestStatus } from './graph-tree.js'

/**
 * Helper: build a fake graph JSON (matching AttackGraph.to_json() output).
 */
function mkGraph(nodes) {
  const byId = {}
  for (const n of nodes) {
    byId[n.id] = {
      id: n.id,
      parent_id: n.parent_id ?? null,
      module: n.module ?? '',
      approach: n.approach ?? '',
      messages_sent: n.messages_sent ?? [],
      target_responses: n.target_responses ?? [],
      score: n.score ?? 0,
      leaked_info: n.leaked_info ?? '',
      reflection: n.reflection ?? '',
      status: n.status ?? 'alive',
      children: n.children ?? [],
      depth: n.depth ?? 0,
      timestamp: 0,
      run_id: '',
      source: n.source ?? 'agent',
    }
  }
  return {
    root_id: nodes.find(n => n.id === 'root')?.id ?? nodes[0]?.id,
    run_counter: 1,
    nodes: byId,
  }
}

describe('bestStatus', () => {
  it('returns promising if any attempt is promising', () => {
    expect(bestStatus(['dead', 'alive', 'promising'])).toBe('promising')
  })
  it('returns alive if no promising but some alive', () => {
    expect(bestStatus(['dead', 'alive', 'dead'])).toBe('alive')
  })
  it('returns dead if all dead', () => {
    expect(bestStatus(['dead', 'dead'])).toBe('dead')
  })
})

describe('buildTree — basic', () => {
  it('returns null for empty input', () => {
    expect(buildTree(null)).toBeNull()
    expect(buildTree({})).toBeNull()
    expect(buildTree({ nodes: {} })).toBeNull()
  })

  it('returns root node with children array', () => {
    const g = mkGraph([
      { id: 'root', module: 'root', children: [] },
    ])
    const tree = buildTree(g)
    expect(tree).not.toBeNull()
    expect(tree.id).toBe('root')
    expect(tree.children).toEqual([])
  })
})

describe('buildTree — frontier filtering', () => {
  it('filters out frontier nodes', () => {
    const g = mkGraph([
      { id: 'root', module: 'root', children: ['a', 'b'] },
      { id: 'a', module: 'foo', status: 'alive', score: 5, children: [] },
      { id: 'b', module: 'bar', status: 'frontier', score: 0, children: [] },
    ])
    const tree = buildTree(g)
    expect(tree.children).toHaveLength(1)
    expect(tree.children[0].module).toBe('foo')
  })

  it('CRITICAL: when all children are frontier, parent has empty children array (not string IDs)', () => {
    // This was the original bug: spreading node.children copies string IDs.
    // If we don't overwrite, d3.hierarchy sees strings as children → "undefined" everywhere.
    const g = mkGraph([
      { id: 'root', module: 'root', children: ['leaf'] },
      { id: 'leaf', module: 'foo', status: 'alive', score: 5,
        children: ['frontier1', 'frontier2', 'frontier3'] },
      { id: 'frontier1', module: 'f1', status: 'frontier', children: [] },
      { id: 'frontier2', module: 'f2', status: 'frontier', children: [] },
      { id: 'frontier3', module: 'f3', status: 'frontier', children: [] },
    ])
    const tree = buildTree(g)
    expect(tree.children).toHaveLength(1)
    const leaf = tree.children[0]
    // children must be an array of OBJECTS, not string IDs
    expect(Array.isArray(leaf.children)).toBe(true)
    expect(leaf.children).toEqual([])
    // Double-check: no string in children
    for (const child of leaf.children) {
      expect(typeof child).toBe('object')
      expect(child.module).toBeDefined()
    }
  })

  it('preserves node fields after spread', () => {
    const g = mkGraph([
      { id: 'root', module: 'root', children: ['a'] },
      { id: 'a', module: 'foo', status: 'alive', score: 5,
        approach: 'try X', leaked_info: 'leaked Y', children: [] },
    ])
    const tree = buildTree(g)
    const a = tree.children[0]
    expect(a.module).toBe('foo')
    expect(a.score).toBe(5)
    expect(a.approach).toBe('try X')
    expect(a.leaked_info).toBe('leaked Y')
    expect(a.status).toBe('alive')
  })
})

describe('buildTree — grouping', () => {
  it('groups same-module siblings', () => {
    const g = mkGraph([
      { id: 'root', module: 'root', children: ['a1', 'a2', 'a3', 'b1'] },
      { id: 'a1', module: 'safety-profiler', status: 'alive', score: 3, children: [] },
      { id: 'a2', module: 'safety-profiler', status: 'alive', score: 3, children: [] },
      { id: 'a3', module: 'safety-profiler', status: 'promising', score: 8, children: [] },
      { id: 'b1', module: 'foo', status: 'alive', score: 4, children: [] },
    ])
    const tree = buildTree(g)

    expect(tree.children).toHaveLength(2)

    const safety = tree.children.find(c => c.module === 'safety-profiler')
    expect(safety.isGroup).toBe(true)
    expect(safety.attemptCount).toBe(3)
    expect(safety.bestScore).toBe(8)
    expect(safety.bestStatus).toBe('promising')
    expect(safety.scores).toEqual([3, 3, 8])

    const foo = tree.children.find(c => c.module === 'foo')
    expect(foo.isGroup).toBeUndefined()  // single, not grouped
  })

  it('group node has proper attempts array with all fields', () => {
    const g = mkGraph([
      { id: 'root', module: 'root', children: ['a1', 'a2'] },
      { id: 'a1', module: 'narrative-transport', status: 'dead', score: 2,
        approach: 'fiction attempt', children: [] },
      { id: 'a2', module: 'narrative-transport', status: 'dead', score: 1,
        approach: 'story attempt', children: [] },
    ])
    const tree = buildTree(g)
    const group = tree.children[0]

    expect(group.attempts).toHaveLength(2)
    // CRITICAL: attempts must have score, module, approach, status — NOT undefined
    for (const attempt of group.attempts) {
      expect(attempt.score).toBeTypeOf('number')
      expect(attempt.module).toBe('narrative-transport')
      expect(attempt.approach).toBeTruthy()
      expect(attempt.status).toBe('dead')
    }
  })

  it('single occurrence does NOT create a group', () => {
    const g = mkGraph([
      { id: 'root', module: 'root', children: ['a'] },
      { id: 'a', module: 'foo', status: 'alive', score: 5, children: [] },
    ])
    const tree = buildTree(g)
    expect(tree.children[0].isGroup).toBeUndefined()
    expect(tree.children[0].module).toBe('foo')
  })

  it('grouped nodes merge their explored children', () => {
    const g = mkGraph([
      { id: 'root', module: 'root', children: ['a1', 'a2'] },
      { id: 'a1', module: 'safety-profiler', status: 'alive', score: 3, children: ['c1'] },
      { id: 'a2', module: 'safety-profiler', status: 'promising', score: 8, children: ['c2'] },
      { id: 'c1', module: 'foo', status: 'alive', score: 4, children: [] },
      { id: 'c2', module: 'bar', status: 'alive', score: 5, children: [] },
    ])
    const tree = buildTree(g)
    const group = tree.children[0]
    expect(group.isGroup).toBe(true)
    // Children from both a1 and a2 should be present under the group
    expect(group.children).toHaveLength(2)
    const mods = group.children.map(c => c.module).sort()
    expect(mods).toEqual(['bar', 'foo'])
  })
})

describe('buildTree — VPA staging scenario (real data)', () => {
  // Recreates the actual graph structure from the user's custom scenario
  it('produces clean tree: 6 visible children from root, no undefined fields', () => {
    const g = mkGraph([
      { id: 'root', module: 'root', status: 'alive',
        children: ['sp1', 'sp2', 'co1', 'ab1', 'an1', 'fid1', 'nt1', 'sp3', 'co2', 'nt2'] },
      { id: 'sp1', module: 'safety-profiler', status: 'alive', score: 3,
        approach: 'Map boundaries', children: [] },
      { id: 'sp2', module: 'safety-profiler', status: 'alive', score: 3,
        approach: 'Map boundaries v2', children: ['f1', 'f2', 'f3'] },
      { id: 'f1', module: 'narrative-transport', status: 'frontier', children: [] },
      { id: 'f2', module: 'authority-bias', status: 'frontier', children: [] },
      { id: 'f3', module: 'foot-in-door', status: 'frontier', children: [] },
      { id: 'co1', module: 'cognitive-overload', status: 'alive', score: 4,
        approach: 'overload', children: ['f4', 'f5', 'f6'] },
      { id: 'f4', module: 'authority-bias', status: 'frontier', children: [] },
      { id: 'f5', module: 'foot-in-door', status: 'frontier', children: [] },
      { id: 'f6', module: 'narrative-transport', status: 'frontier', children: [] },
      { id: 'ab1', module: 'authority-bias', status: 'dead', score: 1,
        approach: 'claim auth', children: [] },
      { id: 'an1', module: 'anchoring', status: 'dead', score: 1,
        approach: 'normalize sharing', children: [] },
      { id: 'fid1', module: 'foot-in-door', status: 'promising', score: 7,
        approach: 'small request', children: ['f7', 'f8', 'fid2'] },
      { id: 'f7', module: 'narrative-transport', status: 'frontier', children: [] },
      { id: 'f8', module: 'system-prompt-extraction', status: 'frontier', children: [] },
      { id: 'fid2', module: 'foot-in-door', status: 'alive', score: 3,
        approach: 'benign request', children: ['f9', 'f10'] },
      { id: 'f9', module: 'safety-profiler', status: 'frontier', children: [] },
      { id: 'f10', module: 'cognitive-overload', status: 'frontier', children: [] },
      { id: 'nt1', module: 'narrative-transport', status: 'dead', score: 2,
        approach: 'fiction story', children: [] },
      { id: 'sp3', module: 'safety-profiler', status: 'promising', score: 8,
        approach: 'systematic probing', children: ['f11', 'f12'] },
      { id: 'f11', module: 'system-prompt-extraction', status: 'frontier', children: [] },
      { id: 'f12', module: 'safety-profiler', status: 'frontier', children: [] },
      { id: 'co2', module: 'cognitive-overload', status: 'alive', score: 3,
        approach: 'overwhelm', children: ['f13', 'f14', 'f15'] },
      { id: 'f13', module: 'system-prompt-extraction', status: 'frontier', children: [] },
      { id: 'f14', module: 'system-prompt-extraction', status: 'frontier', children: [] },
      { id: 'f15', module: 'system-prompt-extraction', status: 'frontier', children: [] },
      { id: 'nt2', module: 'narrative-transport', status: 'dead', score: 1,
        approach: 'fictional char', children: [] },
    ])

    const tree = buildTree(g)
    expect(tree).not.toBeNull()

    // Root has 6 visible children after grouping (6 unique modules)
    expect(tree.children).toHaveLength(6)

    // Check grouping worked
    const safety = tree.children.find(c => c.module === 'safety-profiler')
    expect(safety.isGroup).toBe(true)
    expect(safety.attemptCount).toBe(3)
    expect(safety.scores.sort((a, b) => a - b)).toEqual([3, 3, 8])
    expect(safety.bestScore).toBe(8)
    expect(safety.bestStatus).toBe('promising')

    const nt = tree.children.find(c => c.module === 'narrative-transport')
    expect(nt.isGroup).toBe(true)
    expect(nt.attemptCount).toBe(2)
    expect(nt.bestStatus).toBe('dead')

    const co = tree.children.find(c => c.module === 'cognitive-overload')
    expect(co.isGroup).toBe(true)
    expect(co.attemptCount).toBe(2)

    // foot-in-door is single but has an explored child
    const fid = tree.children.find(c => c.module === 'foot-in-door')
    expect(fid.isGroup).toBeUndefined()
    expect(fid.score).toBe(7)
    expect(fid.children).toHaveLength(1)
    expect(fid.children[0].module).toBe('foot-in-door')

    // Single dead-ends
    const ab = tree.children.find(c => c.module === 'authority-bias')
    expect(ab.isGroup).toBeUndefined()
    expect(ab.status).toBe('dead')

    // VERY IMPORTANT: no undefined fields anywhere in the tree
    function walk(node, path = '') {
      expect(node, `at ${path}`).toBeDefined()
      expect(node.module, `${path}.module`).toBeDefined()
      expect(node.module, `${path}.module`).not.toBe('undefined')
      expect(typeof node.module, `${path}.module type`).toBe('string')

      if (node.isGroup) {
        expect(node.bestScore, `${path}.bestScore`).toBeTypeOf('number')
        expect(node.bestStatus, `${path}.bestStatus`).toBeDefined()
        for (const a of node.attempts) {
          expect(a.score, `${path}.attempts.score`).toBeTypeOf('number')
          expect(a.module, `${path}.attempts.module`).toBeDefined()
          expect(a.status, `${path}.attempts.status`).toBeDefined()
        }
      } else {
        expect(node.status, `${path}.status`).toBeDefined()
      }

      expect(Array.isArray(node.children), `${path}.children must be array`).toBe(true)
      // No string IDs in children (the original bug)
      for (const c of node.children) {
        expect(typeof c, `${path}.children item type`).toBe('object')
      }

      for (const c of node.children) walk(c, `${path}/${c.module}`)
    }
    walk(tree, 'root')
  })
})
