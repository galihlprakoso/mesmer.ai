/**
 * Project an AttackGraph snapshot into the visible leader-rooted tree.
 *
 * The runtime owns parentage. Every execution node has a deterministic
 * `parent_id` assigned when delegation starts:
 *
 *   executive
 *     ├─ manager
 *     │   └─ child module
 *     └─ manager
 *
 * This file must not infer hierarchy from module names, timestamps, active
 * stack, or scenario YAML. Those are display hints at most; `parent_id` is
 * the contract.
 */

const SYNTHETIC_LEADER_ID = '__leader__'

function sortNodes(a, b) {
  return (a.timestamp || 0) - (b.timestamp || 0) || a.id.localeCompare(b.id)
}

function materializeNode(node, childrenByParent, collapsed, depth = 0) {
  const childNodes = (childrenByParent.get(node.id) || []).sort(sortNodes)
  const materializedChildren = collapsed.has(node.id)
    ? []
    : childNodes.map(child => materializeNode(child, childrenByParent, collapsed, depth + 1))

  return {
    ...node,
    depth,
    _childIds: childNodes.map(child => child.id),
    children: materializedChildren,
  }
}

function latestLeader(nodes) {
  const leaders = nodes.filter(n => n.source === 'leader')
  leaders.sort(sortNodes)
  return leaders.at(-1) || null
}

/**
 * @param graphJson      The full graph snapshot.
 * @param collapsed      Set of execution-node ids whose children are hidden.
 * @param scenarioMeta   Optional `{ leaderModule, objective }` for root-only state.
 * @param runId          When set, render exactly that run. `null` keeps the
 *                       cumulative view rooted at the newest executive node.
 */
export function buildLeaderTimeline(
  graphJson,
  collapsed = new Set(),
  scenarioMeta = null,
  runId = null,
) {
  if (!graphJson || !graphJson.nodes) return null

  const nodes = graphJson.nodes
  const rootId = graphJson.root_id
  if (!rootId || !nodes[rootId]) return null

  const executionNodes = Object.values(nodes)
    .filter(n => n.id !== rootId)
    .filter(n => !runId || n.run_id === runId)

  const leader = latestLeader(executionNodes)
  if (!leader) {
    return {
      id: SYNTHETIC_LEADER_ID,
      module: scenarioMeta?.leaderModule || 'leader',
      approach: scenarioMeta?.objective || '',
      status: 'running',
      source: 'synthetic',
      score: 0,
      depth: 0,
      parent_id: null,
      _isLeaderOrchestrator: true,
      _isLeaderRoot: true,
      _scenarioObjective: scenarioMeta?.objective || '',
      _childIds: [],
      children: [],
      messages_sent: [],
      target_responses: [],
      leaked_info: '',
      module_output: '',
      reflection: '',
      agent_trace: [],
    }
  }

  const childrenByParent = new Map()
  for (const node of executionNodes) {
    if (node.id === leader.id) continue
    if (!node.parent_id) continue
    if (!childrenByParent.has(node.parent_id)) childrenByParent.set(node.parent_id, [])
    childrenByParent.get(node.parent_id).push(node)
  }

  const tree = materializeNode(leader, childrenByParent, collapsed, 0)
  tree._isLeaderRoot = true
  tree._scenarioObjective = scenarioMeta?.objective || ''
  tree.children = tree.children.map((child, idx) => ({
    ...child,
    _seqNum: idx + 1,
  }))
  return tree
}

export { SYNTHETIC_LEADER_ID }
