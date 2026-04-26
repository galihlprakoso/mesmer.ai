/**
 * Reshape an AttackGraph snapshot into the leader-rooted timeline view:
 *
 *   leader (square verdict post-run, diamond stub mid-run)
 *     ├─ #1 manager-A
 *     │   ├─ sub-module-x
 *     │   └─ frontier-y (proposed, never ran)
 *     ├─ #2 manager-B
 *     └─ #3 manager-C
 *
 * The persisted `root` node is intentionally NOT rendered — it's a graph-
 * storage artifact, not a meaningful unit for the human watching the run.
 *
 * Two cases for the root of the rendered tree:
 *
 *   - **Run completed** — the leader-verdict node (`source === "leader"`)
 *     IS the root. It carries real data (status=promising/dead,
 *     module_output, leaked_info, messages_sent) so clicking it shows the
 *     leader's actual verdict in NodeDetail.
 *   - **Mid-run / pre-run** — synthesise a stub keyed to the scenario's
 *     leader module. Carries `_isLeaderOrchestrator: true` and
 *     `module: scenarioMeta.leaderModule` so NodeDetail can fetch the
 *     leader module's config and render scenario context.
 *
 * Real delegations are ordered by `timestamp` (the leader's call sequence)
 * and stamped with `_seqNum` so the renderer can label them #1, #2…
 * Delegations made inside known manager modules are nested under that
 * manager so the rendered graph reflects module ownership, not just the
 * underlying append-only attempt log.
 *
 * Frontier proposals stay attached to whichever attempt produced them so
 * the user can see "this attempt's reflection proposed this next move".
 */

const SYNTHETIC_LEADER_ID = '__leader__'

const MANAGER_SUBMODULES = {
  'system-prompt-extraction': new Set([
    'target-profiler',
    'attack-planner',
    'direct-ask',
    'instruction-recital',
    'indirect-recital',
    'format-shift',
    'prefix-commitment',
    'delimiter-injection',
    'role-impersonation',
    'cognitive-overload',
    'foot-in-door',
    'anchoring',
    'authority-bias',
    'narrative-transport',
    'pragmatic-reframing',
  ]),
  'tool-extraction': new Set([
    'target-profiler',
    'attack-planner',
    'direct-ask',
    'instruction-recital',
    'indirect-recital',
    'format-shift',
    'prefix-commitment',
    'delimiter-injection',
    'role-impersonation',
    'cognitive-overload',
    'foot-in-door',
    'anchoring',
    'authority-bias',
    'narrative-transport',
    'pragmatic-reframing',
    'hallucinated-tool-probing',
    'fake-function-injection',
  ]),
  'exploit-analysis': new Set([
    'target-profiler',
    'attack-planner',
    'direct-ask',
    'instruction-recital',
    'indirect-recital',
    'format-shift',
    'prefix-commitment',
    'delimiter-injection',
    'role-impersonation',
    'cognitive-overload',
    'foot-in-door',
    'anchoring',
    'authority-bias',
    'narrative-transport',
    'pragmatic-reframing',
  ]),
  'exploit-executor': new Set([
    'target-profiler',
    'attack-planner',
    'direct-ask',
    'instruction-recital',
    'indirect-recital',
    'format-shift',
    'prefix-commitment',
    'delimiter-injection',
    'role-impersonation',
    'cognitive-overload',
    'foot-in-door',
    'anchoring',
    'authority-bias',
    'narrative-transport',
    'pragmatic-reframing',
    'hallucinated-tool-probing',
    'fake-function-injection',
  ]),
}

function attachFrontier(parentId, frontierByParent) {
  const props = frontierByParent[parentId] || []
  return props.map(n => ({
    ...n,
    _childIds: n.children || [],
    children: [], // frontier proposals are leaves in the timeline view
  }))
}

function materializeAttempt(n, collapsed, frontierByParent, children = []) {
  const frontierChildren = collapsed.has(n.id) ? [] : attachFrontier(n.id, frontierByParent)
  return {
    ...n,
    _childIds: n.children || [],
    children: collapsed.has(n.id) ? [] : [...children, ...frontierChildren],
  }
}

function isOrderedManagerFrontier(n, nodes, scenarioMeta) {
  const ordered = new Set(Array.isArray(scenarioMeta?.modules) ? scenarioMeta.modules : [])
  if (!ordered.size || n.status !== 'frontier') return false
  const parent = nodes[n.parent_id]
  return !!parent && ordered.has(parent.module) && ordered.has(n.module)
}

function synthesizeMissingManager(ranAttempts, managerAttempts, scenarioMeta, activeModuleTop) {
  const ordered = Array.isArray(scenarioMeta?.modules) ? scenarioMeta.modules : []
  if (!activeModuleTop || !ordered.includes(activeModuleTop)) return null
  const candidates = [activeModuleTop]
  const existing = new Set(managerAttempts.map(n => n.module))

  for (const managerName of candidates) {
    const submodules = MANAGER_SUBMODULES[managerName]
    if (!submodules || existing.has(managerName)) continue

    const children = ranAttempts.filter(n =>
      !MANAGER_SUBMODULES[n.module] && submodules.has(n.module)
    )
    if (!children.length) continue

    const ts = Math.max(...children.map(n => n.timestamp || 0)) + 0.001
    return {
      id: `__manager__${managerName}`,
      module: managerName,
      approach: 'active manager',
      status: 'alive',
      source: 'synthetic',
      score: 0,
      depth: 1,
      parent_id: SYNTHETIC_LEADER_ID,
      timestamp: ts,
      run_id: children[0]?.run_id || '',
      children: children.map(n => n.id),
      messages_sent: [],
      target_responses: [],
      leaked_info: '',
      module_output: '',
      reflection: '',
      _isSyntheticManager: true,
    }
  }
  return null
}

/**
 * @param graphJson      The full graph snapshot.
 * @param collapsed      Set of attempt-node ids whose frontier children are hidden.
 * @param scenarioMeta   Optional `{ leaderModule, objective }` for the synthetic stub.
 * @param runId          When set, filter the rendered tree to a single run —
 *                       only attempts / frontiers / leader-verdict from that
 *                       run survive. `null` keeps the cumulative cross-run view.
 */
export function buildLeaderTimeline(
  graphJson,
  collapsed = new Set(),
  scenarioMeta = null,
  runId = null,
  activeModuleTop = null,
) {
  if (!graphJson || !graphJson.nodes) return null

  const nodes = graphJson.nodes
  const rootId = graphJson.root_id
  if (!rootId || !nodes[rootId]) return null

  const ranAttempts = []
  const frontierByParent = {}
  let leaderVerdict = null

  for (const [id, n] of Object.entries(nodes)) {
    if (id === rootId) continue
    // Per-run filter: skip everything that doesn't belong to the
    // requested run. Applies uniformly to attempts, frontiers, and
    // leader-verdicts so the rendered tree is exactly one run's worth.
    if (runId && n.run_id !== runId) continue
    if (n.source === 'leader') {
      // Pick THIS run's verdict (if filtered), otherwise the most-recent
      // verdict overall — matches "All runs" historic behaviour.
      if (!leaderVerdict || (n.timestamp || 0) > (leaderVerdict.timestamp || 0)) {
        leaderVerdict = n
      }
      continue
    }
    if (n.status === 'frontier') {
      if (isOrderedManagerFrontier(n, nodes, scenarioMeta)) continue
      const p = n.parent_id || rootId
      if (!frontierByParent[p]) frontierByParent[p] = []
      frontierByParent[p].push(n)
      continue
    }
    ranAttempts.push(n)
  }

  // Order delegations by leader's call sequence — fall back to id for
  // synthetic graphs without timestamps so tests stay deterministic.
  ranAttempts.sort((a, b) =>
    (a.timestamp || 0) - (b.timestamp || 0) || a.id.localeCompare(b.id)
  )

  const managerAttempts = ranAttempts.filter(n => MANAGER_SUBMODULES[n.module])
  const syntheticManager = synthesizeMissingManager(
    ranAttempts,
    managerAttempts,
    scenarioMeta,
    activeModuleTop,
  )
  const allAttempts = syntheticManager ? [...ranAttempts, syntheticManager] : ranAttempts
  const allManagerAttempts = syntheticManager
    ? [...managerAttempts, syntheticManager]
    : managerAttempts
  const assignedChildIds = new Set()
  const childIdsByManager = {}

  for (const n of ranAttempts) {
    if (MANAGER_SUBMODULES[n.module]) continue
    const owner = allManagerAttempts.find(m =>
      (n.timestamp || 0) <= (m.timestamp || 0)
      && MANAGER_SUBMODULES[m.module]?.has(n.module)
    )
    if (!owner) continue
    assignedChildIds.add(n.id)
    if (!childIdsByManager[owner.id]) childIdsByManager[owner.id] = []
    childIdsByManager[owner.id].push(n)
  }

  const topAttempts = allAttempts
    .filter(n => !assignedChildIds.has(n.id))
    .sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0) || a.id.localeCompare(b.id))
  const attemptChildren = topAttempts.map((n, idx) => {
    const ownedChildren = (childIdsByManager[n.id] || [])
      .sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0) || a.id.localeCompare(b.id))
      .map(child => materializeAttempt(child, collapsed, frontierByParent))
    return {
      ...materializeAttempt(n, collapsed, frontierByParent, ownedChildren),
      _seqNum: idx + 1,
    }
  })

  // Run concluded — real verdict node IS the root, with attempts as children.
  if (leaderVerdict) {
    return {
      ...leaderVerdict,
      _childIds: leaderVerdict.children || [],
      _isLeaderRoot: true,
      children: attemptChildren,
    }
  }

  // Mid-run / pre-run — synthesise a stub keyed to the scenario's leader.
  const leaderModuleName = scenarioMeta?.leaderModule || 'leader'
  return {
    id: SYNTHETIC_LEADER_ID,
    module: leaderModuleName,
    approach: scenarioMeta?.objective || '',
    status: 'alive',
    source: 'synthetic',
    score: 0,
    depth: 0,
    parent_id: null,
    _isLeaderOrchestrator: true,
    _isLeaderRoot: true,
    _scenarioObjective: scenarioMeta?.objective || '',
    _childIds: attemptChildren.map(c => c.id),
    children: attemptChildren,
    messages_sent: [],
    target_responses: [],
    leaked_info: '',
    module_output: '',
    reflection: '',
  }
}

export { SYNTHETIC_LEADER_ID }
