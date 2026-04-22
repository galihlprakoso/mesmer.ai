/**
 * WebSocket client with automatic reconnection.
 */

let ws = null
let reconnectTimer = null
let reconnectDelay = 1000
const MAX_RECONNECT_DELAY = 30000

const listeners = new Set()

export function onMessage(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function getWsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}/ws`
}

export function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return
  }

  ws = new WebSocket(getWsUrl())

  ws.onopen = () => {
    reconnectDelay = 1000
    listeners.forEach(fn => fn({ type: 'ws_status', status: 'connected' }))
  }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      listeners.forEach(fn => fn(data))
    } catch (e) {
      console.warn('Invalid WS message:', event.data)
    }
  }

  ws.onclose = () => {
    listeners.forEach(fn => fn({ type: 'ws_status', status: 'disconnected' }))
    scheduleReconnect()
  }

  ws.onerror = () => {
    ws.close()
  }
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer)
  reconnectTimer = setTimeout(() => {
    reconnectDelay = Math.min(reconnectDelay * 1.5, MAX_RECONNECT_DELAY)
    connect()
  }, reconnectDelay)
}

export function send(data) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(data))
  }
}

export function disconnect() {
  clearTimeout(reconnectTimer)
  if (ws) {
    ws.close()
    ws = null
  }
}
