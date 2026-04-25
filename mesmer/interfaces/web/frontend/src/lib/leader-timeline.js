/**
 * Reshape an AttackGraph snapshot into the leader-rooted timeline view:
 *
 *   leader (square verdict post-run, diamond stub mid-run)
 *     ├─ #1 attempt-A
 *     │   └─ frontier-x (proposed, never ran)
 *     ├─ #2 attempt-B
 *     └─ #3 attempt-C
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
 *
 * Frontier proposals stay attached to whichever attempt produced them so
 * the user can see "this attempt's reflection proposed this next move".
 */

const SYNTHETIC_LEADER_ID = '__leader__'

function attachFrontier(parentId, frontierByParent) {
  const props = frontierByParent[parentId] || []
  return props.map(n => ({
    ...n,
    _childIds: n.children || [],
    children: [], // frontier proposals are leaves in the timeline view
  }))
}

/**
 * @param graphJson      The full graph snapshot.
 * @param collapsed      Set of attempt-node ids whose frontier children are hidden.
 * @param scenarioMeta   Optional `{ leaderModule, objective }` for the synthetic stub.
 * @param runId          When set, filter the rendered tree to a single run —
 *                       only attempts / frontiers / leader-verdict from that
 *                       run survive. `null` keeps the cumulative cross-run view.
 */
export function buildLeaderTimeline(graphJson, collapsed = new Set(), scenarioMeta = null, runId = null) {
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

  const attemptChildren = ranAttempts.map((n, idx) => ({
    ...n,
    _childIds: n.children || [],
    _seqNum: idx + 1,
    children: collapsed.has(n.id) ? [] : attachFrontier(n.id, frontierByParent),
  }))

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
