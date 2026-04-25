/**
 * Centralized fetch helpers for the new pages. Existing components keep
 * their inline fetches for now — migrate opportunistically.
 *
 * Every helper throws on non-2xx so callers can `try/catch` once;
 * the response error string is whatever the backend put in
 * `{error: "..."}` (the house style).
 */

async function unwrap(res) {
  if (res.ok) return res.json()
  let body = {}
  try {
    body = await res.json()
  } catch {
    /* empty */
  }
  throw new Error(body.error || `HTTP ${res.status}`)
}

export async function listScenarios() {
  const res = await fetch('/api/scenarios')
  return unwrap(res)
}

export async function loadScenario(path) {
  const res = await fetch(`/api/scenarios/${encodeURIComponent(path)}`)
  return unwrap(res)
}

export async function createScenario(name, yamlContent) {
  const res = await fetch('/api/scenarios', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, yaml_content: yamlContent }),
  })
  return unwrap(res)
}

export async function updateScenario(path, yamlContent) {
  const res = await fetch(`/api/scenarios/${encodeURIComponent(path)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ yaml_content: yamlContent }),
  })
  return unwrap(res)
}

export async function validateScenario(yamlContent) {
  const res = await fetch('/api/scenarios/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ yaml_content: yamlContent }),
  })
  return unwrap(res)
}

export async function editorChat(yamlContent, message, history) {
  const res = await fetch('/api/scenario-editor-chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ yaml_content: yamlContent, message, history }),
  })
  return unwrap(res)
}

export async function listModules() {
  const res = await fetch('/api/modules')
  return unwrap(res)
}
