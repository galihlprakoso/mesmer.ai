/**
 * Parse the name of the module being started from a `module_start` event's detail string.
 *
 * Backend format (loop.py):  "${module.name} — tools: tool1, tool2, ..."
 * Where the separator is an em-dash (U+2014).
 */
export function parseModuleStart(detail) {
  if (!detail) return null
  // Match everything before the em-dash (with surrounding spaces)
  const idx = detail.indexOf('\u2014')  // em-dash
  if (idx < 0) {
    // Fallback: first whitespace-separated token
    return detail.trim().split(/\s+/)[0] || null
  }
  return detail.slice(0, idx).trim() || null
}

/**
 * Given the current active-module stack and a new event, produce the next stack.
 * Pure function — easily testable.
 *
 * Semantics:
 *   - module_start(X)  → push X
 *   - conclude(X?)     → pop top (we can't reliably parse name from the conclude detail
 *                         because it's a natural-language summary, but since modules
 *                         are strictly nested, popping the top matches correctly)
 *   - hard_stop        → pop (the module refused to act, loop bailed out of that level)
 *   - status: completed | error | stopped → clear the stack
 *
 * Any other event passes through unchanged.
 */
export function nextActiveStack(stack, evt) {
  if (!evt) return stack

  if (evt.type === 'status' && ['completed', 'error', 'stopped', 'idle'].includes(evt.status)) {
    return []
  }

  if (evt.type !== 'event') return stack

  switch (evt.event) {
    case 'module_start': {
      const name = parseModuleStart(evt.detail)
      if (!name) return stack
      return [...stack, name]
    }
    case 'conclude':
    case 'hard_stop':
      return stack.slice(0, -1)
    default:
      return stack
  }
}

/** Fold an array of events into the final active-module stack (initial = []). */
export function activeStackFromEvents(events) {
  let stack = []
  for (const evt of events) {
    stack = nextActiveStack(stack, evt)
  }
  return stack
}
