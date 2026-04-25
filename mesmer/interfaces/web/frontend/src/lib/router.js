/**
 * Hash-based router for the mesmer web UI.
 *
 *   #/                                  → list
 *   #/scenarios/new                     → editor (blank)
 *   #/scenarios/{path}/edit             → editor (existing)
 *   #/scenarios/{path}                  → graph view
 *
 * `path` is URL-encoded so segment slashes survive (`scenarios/foo.yaml`
 * lives in the hash as `scenarios%2Ffoo.yaml`).
 */

import { writable } from 'svelte/store'

const BLANK_ROUTE = { view: 'list', scenarioPath: null, isNew: false }

/** @type {import('svelte/store').Writable<{view: 'list'|'editor'|'graph', scenarioPath: string|null, isNew: boolean}>} */
export const currentRoute = writable(BLANK_ROUTE)

export function parseHash(hash) {
  // Strip leading "#" and "/"
  let h = (hash || '').replace(/^#/, '').replace(/^\//, '')
  if (!h) return { ...BLANK_ROUTE }

  if (h === 'scenarios/new') {
    return { view: 'editor', scenarioPath: null, isNew: true }
  }

  // scenarios/<encoded-path>/edit  or  scenarios/<encoded-path>
  const m = h.match(/^scenarios\/(.+?)(\/edit)?$/)
  if (m) {
    const decoded = safeDecode(m[1])
    if (m[2]) {
      return { view: 'editor', scenarioPath: decoded, isNew: false }
    }
    return { view: 'graph', scenarioPath: decoded, isNew: false }
  }

  // Unknown route → fall back to list
  return { ...BLANK_ROUTE }
}

function safeDecode(s) {
  try {
    return decodeURIComponent(s)
  } catch {
    return s
  }
}

export function navigate(view, scenarioPath = null) {
  let hash = '#/'
  if (view === 'editor' && !scenarioPath) {
    hash = '#/scenarios/new'
  } else if (view === 'editor' && scenarioPath) {
    hash = `#/scenarios/${encodeURIComponent(scenarioPath)}/edit`
  } else if (view === 'graph' && scenarioPath) {
    hash = `#/scenarios/${encodeURIComponent(scenarioPath)}`
  }
  if (window.location.hash !== hash) {
    window.location.hash = hash
  } else {
    // Force a re-parse if the user clicked the same link.
    currentRoute.set(parseHash(hash))
  }
}

export function init() {
  const apply = () => currentRoute.set(parseHash(window.location.hash))
  window.addEventListener('hashchange', apply)
  apply()
}
