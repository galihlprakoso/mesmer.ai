/**
 * Leader-chat API client. Thin wrappers around /api/chat and /api/leader-chat.
 */

export async function fetchChat(scenarioPath, limit = 20) {
  const url = `/api/chat?scenario_path=${encodeURIComponent(scenarioPath)}&limit=${limit}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`fetchChat failed: HTTP ${res.status}`)
  return res.json() // { items: [{role, content, timestamp}, ...] }
}

export async function sendLeaderChat(scenarioPath, message) {
  const res = await fetch('/api/leader-chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario_path: scenarioPath, message }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.error || `HTTP ${res.status}`)
  }
  return res.json()
  // Idle -> { queued:false, reply, tool_trace, updated_artifact }
  // Mid-run -> { queued:true, reply:null, tool_trace:[], updated_artifact:null }
}
