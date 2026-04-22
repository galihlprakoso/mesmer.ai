/**
 * Build a clean hierarchy tree from graph JSON for D3 rendering:
 *   1. Filter out all frontier nodes entirely
 *   2. Group same-module siblings into virtual group nodes
 *
 * Extracted from AttackGraph.svelte so it can be unit-tested.
 */

function bestStatus(statuses) {
  if (statuses.includes('promising')) return 'promising'
  if (statuses.includes('alive')) return 'alive'
  return 'dead'
}

function groupSiblings(children) {
  const byModule = {}
  const order = []

  for (const child of children) {
    const mod = child.module || child.id
    if (!byModule[mod]) {
      byModule[mod] = []
      order.push(mod)
    }
    byModule[mod].push(child)
  }

  const grouped = []
  for (const mod of order) {
    const nodes = byModule[mod]
    if (nodes.length === 1) {
      grouped.push(nodes[0])
    } else {
      const scores = nodes.map(n => n.score || 0)
      const statuses = nodes.map(n => n.status)
      const best = bestStatus(statuses)
      const bestScore = Math.max(...scores)

      const allGroupChildren = []
      for (const n of nodes) {
        if (Array.isArray(n.children)) allGroupChildren.push(...n.children)
      }

      const group = {
        id: 'group-' + mod + '-' + nodes[0].id,
        module: mod,
        isGroup: true,
        attempts: nodes,
        attemptCount: nodes.length,
        bestScore,
        bestStatus: best,
        scores,
        statuses,
        score: bestScore,
        status: best,
        source: 'agent',
        depth: nodes[0].depth,
        leaked_info: [...nodes].sort((a, b) => (b.score || 0) - (a.score || 0))[0].leaked_info || '',
        children: allGroupChildren.length > 0 ? groupSiblings(allGroupChildren) : [],
      }

      grouped.push(group)
    }
  }
  return grouped
}

export function buildTree(graphJson) {
  if (!graphJson || !graphJson.nodes) return null

  const allNodes = graphJson.nodes
  const rootNode = Object.values(allNodes).find(
    n => n.id === 'root' || n.id === graphJson.root_id
  )
  if (!rootNode) return null

  function buildNode(nodeId) {
    const node = allNodes[nodeId]
    if (!node) return null
    // Filter out frontier nodes entirely
    if (node.status === 'frontier') return null

    const result = { ...node }

    // IMPORTANT: spread copies `children` as array of string IDs from the raw JSON.
    // We must always overwrite it with built node objects (or empty array),
    // otherwise d3.hierarchy will try to treat string IDs as nodes → undefined everywhere.
    const childIds = Array.isArray(node.children) ? node.children : []
    const builtChildren = childIds
      .map(id => buildNode(id))
      .filter(Boolean)

    result.children = builtChildren.length > 0
      ? groupSiblings(builtChildren)
      : []

    return result
  }

  return buildNode(rootNode.id)
}

export { bestStatus, groupSiblings }
