/**
 * Svelte stores for mesmer web UI state.
 */

import { writable, derived } from 'svelte/store'
import { buildChatMessages } from './chat-transform.js'
import { nextActiveStack } from './module-tracker.js'

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

/** Selected scenario path */
export const selectedScenario = writable(null)

/** Selected graph node (for detail panel) */
export const selectedNode = writable(null)

/** Co-pilot mode: 'autonomous' | 'co-op' | 'plan'. */
export const mode = writable('autonomous')

/** Whether the Trace (raw events) overlay is shown inside the chat panel. */
export const showTrace = writable(false)

/** Whether the Modules drawer is open (toggled from Sidebar). */
export const modulesDrawerOpen = writable(false)

/**
 * The current agent question awaiting a human answer (Co-op mode).
 * Shape: { question_id, question, options, context, module, timestamp } or null.
 */
export const pendingQuestion = writable(null)

/** Current plan.md content (string, possibly empty). */
export const planDoc = writable('')

/** Whether plan.md exists on disk for this target. */
export const planExists = writable(false)

/**
 * Plan-mode chat — separate from the attack chat. In-memory only (resets
 * when switching scenarios). Array of { role: 'user' | 'assistant', content, timestamp }.
 */
export const planChatMessages = writable([])

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

/** Derived: chronological chat messages for the Co-pilot panel. */
export const chatMessages = derived([graphData, events], ([$g, $evts]) =>
  buildChatMessages($g, $evts)
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

/** Reset plan-mode state (e.g., on scenario change). */
export function resetPlanState() {
  planChatMessages.set([])
  planDoc.set('')
  planExists.set(false)
}

// Clear plan chat when the scenario changes (so hints/plan don't cross-pollute)
let _lastScenario = null
selectedScenario.subscribe(path => {
  if (path !== _lastScenario) {
    _lastScenario = path
    planChatMessages.set([])
  }
})
