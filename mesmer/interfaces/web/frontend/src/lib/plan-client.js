/**
 * Plan-mode API client. Thin wrappers around /api/plan*.
 */

export async function fetchPlan(scenarioPath) {
  const url = `/api/plan?scenario_path=${encodeURIComponent(scenarioPath)}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`fetchPlan failed: HTTP ${res.status}`)
  return res.json()  // { content, exists }
}

export async function savePlan(scenarioPath, content) {
  const res = await fetch('/api/plan', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario_path: scenarioPath, content }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.error || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function planChat(scenarioPath, messages) {
  const res = await fetch('/api/plan/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario_path: scenarioPath, messages }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.error || `HTTP ${res.status}`)
  }
  return res.json()  // { reply, updated_plan | null }
}
