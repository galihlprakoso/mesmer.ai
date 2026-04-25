/**
 * Svelte stores for mesmer web UI state.
 */

import { writable, derived } from 'svelte/store'
import { buildChatMessages } from './chat-transform.js'
import { nextActiveStack } from './module-tracker.js'
import { currentRoute } from './router.js'

/** WebSocket connection status */
export const wsStatus = writable('disconnected')

/** Current run status: idle | running | completed | error | stopped */
export const runStatus = writable('idle')

/** Run metadata (scenario, run_id, result, etc.) */
export const runMeta = writable({})

/** Attack graph data (from server) */
export const graphData = writable(null)

/** Graph stats */
export const graphStats = writable(null)

/** Event feed — array of event objects */
export const events = writable([])

/** Max events to keep in memory */
const MAX_EVENTS = 500

/** Available scenarios */
export const scenarios = writable([])

/** Available modules */
export const modules = writable([])

/** Map of module name → tier (0–3); falls back to DEFAULT_TIER (2). */
export const moduleTiers = derived(modules, $mods =>
  Object.fromEntries(($mods ?? []).map(m => [m.name, m.tier ?? 2]))
)

/** Selected scenario path */
export const selectedScenario = writable(null)

/** Selected graph node (for detail panel) */
export const selectedNode = writable(null)

/** Co-pilot mode: 'autonomous' | 'co-op'.
 *  ('plan' was removed — the leader-chat works in both states now.) */
export const mode = writable('autonomous')

/** Whether the Modules drawer is open (toggled from Sidebar). */
export const modulesDrawerOpen = writable(false)

/**
 * The current agent question awaiting a human answer (Co-op mode).
 * Shape: { question_id, question, options, context, module, timestamp } or null.
 */
export const pendingQuestion = writable(null)

/** Current scratchpad.md content (the leader's persistent notes for the
 *  selected scenario's target). */
export const scratchpadDoc = writable('')

/** Whether scratchpad.md exists on disk for this target. */
export const scratchpadExists = writable(false)

/**
 * Persisted operator <> leader chat for the selected scenario. Loaded from
 * /api/chat on scenario switch; appended to as the user types and as
 * operator_reply WS events arrive. Array of {role, content, timestamp}.
 */
export const chatHistory = writable([])

/** Whether the scratchpad drawer is open. */
export const scratchpadDrawerOpen = writable(false)

/** Derived: is a run in progress? */
export const isRunning = derived(runStatus, $s => $s === 'running')

/**
 * API-key pool status emitted by the backend.
 * Shape: { active, total, keys: [{masked, cooled_until, reason}, ...] } or null.
 * `cooled_until` is unix seconds (0 means active).
 */
export const keyStatus = writable(null)

/**
 * Stack of module names the agent is currently inside (leader at the bottom,
 * most recently delegated-to at the top). Updated incrementally as events
 * arrive in `handleMessage` — see `module-tracker.js` for the semantics.
 */
export const activeModules = writable([])

/** Derived: Set of active module names for O(1) lookup during graph render. */
export const activeModuleSet = derived(activeModules, $stack => new Set($stack))

/** Derived: the current leaf module the agent is working inside (or null). */
export const activeModuleTop = derived(activeModules, $stack =>
  $stack.length > 0 ? $stack[$stack.length - 1] : null
)

/** Currently-selected run for graph filtering. `null` means "All runs"
 *  (the cumulative cross-run view). */
export const selectedRunId = writable(null)

/** Derived: list of unique runs in the graph, oldest-first.
 *
 *  Each entry: { runId, seq, timestamp, verdict }
 *    - seq: 1-based ordinal by timestamp (oldest = #1, newest = #N)
 *    - timestamp: earliest node ts in the run (when it started)
 *    - verdict: 'met' | 'not_met' | 'pending'
 *               (read from the run's leader-verdict node, or pending if none)
 *
 *  RunPicker reads this to render its chip strip. The "is currently
 *  running" flag is derived component-side from `runStatus` + the
 *  newest run's id (the live run is always the newest). */
export const runs = derived(graphData, $g => {
  if (!$g || !$g.nodes) return []
  // Map runId -> { earliestTs, verdict }
  const byRun = new Map()
  for (const node of Object.values($g.nodes)) {
    const rid = node.run_id
    if (!rid) continue  // root node has no run_id
    let entry = byRun.get(rid)
    if (!entry) {
      entry = { runId: rid, timestamp: node.timestamp || 0, verdict: 'pending' }
      byRun.set(rid, entry)
    } else if ((node.timestamp || 0) < entry.timestamp || entry.timestamp === 0) {
      entry.timestamp = node.timestamp || entry.timestamp
    }
    if (node.source === 'leader') {
      entry.verdict = node.status === 'promising' ? 'met' : 'not_met'
    }
  }
  const list = Array.from(byRun.values())
  list.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0))
  list.forEach((r, i) => { r.seq = i + 1 })
  return list
})

/** Derived: chronological chat messages for the Co-pilot panel.
 *
 * Combines the persisted operator <> leader chat (warm-loaded from
 * /api/chat) with conversational events from the live event stream
 * (ask_human, human_answer + legacy human-source frontier hints).
 * Per-attempt outcomes do NOT appear here — those live in the graph. */
export const chatMessages = derived(
  [graphData, events, chatHistory],
  ([$g, $evts, $hist]) => {
    const conv = buildChatMessages($g, $evts)
    const persisted = ($hist || []).map(row => ({
      kind: row.role === 'user' ? 'human' : 'agent-status',
      sender: row.role === 'user' ? 'human' : 'agent',
      timestamp: row.timestamp || 0,
      text: row.content || '',
    }))
    const merged = [...persisted, ...conv]
    merged.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0))
    return merged
  }
)

/** Derived: frontier nodes extracted from graph data */
export const frontierNodes = derived(graphData, $g => {
  if (!$g || !$g.nodes) return []
  return Object.values($g.nodes)
    .filter(n => n.status === 'frontier')
    .sort((a, b) => {
      // Human hints first, then by parent score (higher = more promising lead)
      if (a.source === 'human' && b.source !== 'human') return -1
      if (b.source === 'human' && a.source !== 'human') return 1
      return (b.score || 0) - (a.score || 0)
    })
})

/** Derived: stats that reflect the VISIBLE graph (after filtering frontier). */
export const visibleStats = derived(graphData, $g => {
  if (!$g || !$g.nodes) return null

  const nodes = Object.values($g.nodes)
  // Explored = everything except frontier and root
  const explored = nodes.filter(n => n.status !== 'frontier' && n.module !== 'root')

  const byStatus = { dead: 0, promising: 0, alive: 0 }
  const uniqueModules = new Set()
  let bestScore = 0
  let totalFrontier = 0

  for (const n of nodes) {
    if (n.status === 'frontier') { totalFrontier++; continue }
    if (n.module === 'root') continue
    uniqueModules.add(n.module)
    byStatus[n.status] = (byStatus[n.status] || 0) + 1
    if (n.score > bestScore) bestScore = n.score
  }

  return {
    attempts: explored.length,
    techniques: uniqueModules.size,
    bestScore,
    dead: byStatus.dead,
    promising: byStatus.promising,
    alive: byStatus.alive,
    frontier: totalFrontier,
  }
})

/**
 * Process an incoming WebSocket message and update stores.
 */
export function handleMessage(msg) {
  switch (msg.type) {
    case 'ws_status':
      wsStatus.set(msg.status)
      break

    case 'status':
      // Co-op question/answer ride on the generic status channel
      if (msg.status === 'human_question') {
        pendingQuestion.set({
          question_id: msg.question_id,
          question: msg.question,
          options: msg.options || [],
          context: msg.context || '',
          module: msg.module || '',
          timestamp: msg.timestamp,
        })
        break
      }
      if (msg.status === 'human_answered') {
        pendingQuestion.set(null)
        break
      }
      // API-key pool update — rides the status channel too
      if (msg.status === 'key_status') {
        keyStatus.set({
          active: msg.active ?? 0,
          total: msg.total ?? 0,
          keys: msg.keys || [],
        })
        break
      }
      // Otherwise it's a regular run status change
      runStatus.set(msg.status)
      if (msg.result || msg.run_id || msg.scenario) {
        runMeta.update(m => ({ ...m, ...msg }))
      }
      if (msg.graph_stats) {
        graphStats.set(msg.graph_stats)
      }
      // Clear any pending question + active-module stack if the run ends
      if (['completed', 'error', 'stopped'].includes(msg.status)) {
        pendingQuestion.set(null)
        activeModules.set(nextActiveStack([], msg))
      }
      break

    case 'event':
      events.update(evts => {
        const next = [...evts, msg]
        return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next
      })
      // Track active-module stack incrementally
      activeModules.update(stack => nextActiveStack(stack, msg))

      // Operator <> leader chat events (mid-run): the running leader called
      // talk_to_operator → the reply lands in the chat panel. The user
      // message echo is already optimistically appended client-side, so we
      // drop OPERATOR_MESSAGE here to avoid duplicates.
      if (msg.event === 'operator_reply') {
        chatHistory.update(h => [
          ...h,
          { role: 'assistant', content: msg.detail || '', timestamp: msg.timestamp || Date.now() / 1000 },
        ])
      }
      // Scratchpad mutated mid-run by the leader's update_scratchpad tool.
      // The drawer auto-refreshes on its own via this signal.
      if (msg.event === 'scratchpad_updated') {
        scratchpadExists.set(true)
      }
      break

    case 'graph':
      graphData.set(msg.data)
      if (msg.stats) {
        graphStats.set(msg.stats)
      }
      break
  }
}

/**
 * Reset stores for a new run.
 */
export function resetForNewRun() {
  // Clear only run-scoped transient state. Keep graphData/graphStats so the
  // saved graph (loaded when the scenario was selected) stays visible until
  // the first new snapshot arrives — avoids an "empty graph" flash.
  events.set([])
  selectedNode.set(null)
  runMeta.set({})
  runStatus.set('idle')
  pendingQuestion.set(null)
  activeModules.set([])
  keyStatus.set(null)
}

/** Reset scratchpad + chat-history stores (e.g., on scenario change). */
export function resetScratchpadState() {
  chatHistory.set([])
  scratchpadDoc.set('')
  scratchpadExists.set(false)
}

// Clear chat + scratchpad when the scenario changes so per-target context
// doesn't bleed across scenarios. CoPilotChat refetches /api/chat and
// /api/scratchpad after the reset.
let _lastScenario = null
selectedScenario.subscribe(path => {
  if (path !== _lastScenario) {
    _lastScenario = path
    resetScratchpadState()
  }
})

// Drive `selectedScenario` from the route. The graph view loads the
// scenario; the list and editor views clear it so leftover graph data
// doesn't bleed across views. The route is the single source of truth.
currentRoute.subscribe(route => {
  if (route.view === 'graph' && route.scenarioPath) {
    selectedScenario.set(route.scenarioPath)
  } else {
    selectedScenario.set(null)
    graphData.set(null)
    graphStats.set(null)
  }
})
