/**
 * Scratchpad + leader-chat API client. Thin wrappers around
 * /api/scratchpad, /api/chat, /api/leader-chat.
 */

export async function fetchScratchpad(scenarioPath) {
  const url = `/api/scratchpad?scenario_path=${encodeURIComponent(scenarioPath)}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`fetchScratchpad failed: HTTP ${res.status}`)
  return res.json() // { content, exists }
}

export async function saveScratchpad(scenarioPath, content) {
  const res = await fetch('/api/scratchpad', {
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
  // Idle → { queued:false, reply, tool_trace, updated_scratchpad }
  // Mid-run → { queued:true, reply:null, tool_trace:[], updated_scratchpad:null }
}
