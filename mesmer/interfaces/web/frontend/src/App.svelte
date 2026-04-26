<script>
  import { onMount, onDestroy } from 'svelte'
  import { connect, disconnect, onMessage } from './lib/ws.js'
  import { handleMessage, modulesDrawerOpen, selectedNode, isRunning, mode, selectedTargetHash } from './lib/stores.js'
  import { currentRoute } from './lib/router.js'
  import Sidebar from './components/Sidebar.svelte'
  import AttackGraph from './components/AttackGraph.svelte'
  import BeliefMap from './components/BeliefMap.svelte'
  import NodeDetail from './components/NodeDetail.svelte'
  import ActivityPanel from './components/ActivityPanel.svelte'
  import CoPilotChat from './components/CoPilotChat.svelte'
  import ModuleBrowser from './components/ModuleBrowser.svelte'
  import ScratchpadDrawer from './components/ScratchpadDrawer.svelte'
  import ScenarioList from './pages/ScenarioList.svelte'
  import ScenarioEditor from './pages/ScenarioEditor.svelte'

  let unsubscribe
  let sidebarOpen = false
  // Graph view toggle: 'attack' shows the legacy AttackGraph (attempt
  // history tree); 'belief' shows the typed Belief Attack Graph (the
  // planner's belief landscape). Both run side-by-side at the data
  // level — the toggle just picks which one to render.
  let graphView = 'attack'
  // Default the bottom (chat) panel open — under the executive layer
  // every run is conversational by default; the chat IS the primary
  // operator surface during runs. Operator can still collapse via
  // the edge toggle.
  let bottomOpen = true

  onMount(() => {
    connect()
    unsubscribe = onMessage(handleMessage)
  })

  onDestroy(() => {
    if (unsubscribe) unsubscribe()
    disconnect()
  })
</script>

{#if $currentRoute.view === 'list'}
  <ScenarioList />
{:else if $currentRoute.view === 'editor'}
  <ScenarioEditor path={$currentRoute.scenarioPath} />
{:else}

<div class="app">
  <div
    id="app-sidebar"
    class="sidebar-host"
    class:open={sidebarOpen}
    inert={!sidebarOpen}
  >
    <Sidebar />
  </div>

  <main class="main">
    <div class="center-area">
      <div class="graph-area">
        <div class="graph-view-toggle" role="tablist" aria-label="Graph view">
          <button
            type="button"
            role="tab"
            aria-selected={graphView === 'attack'}
            class="view-tab"
            class:active={graphView === 'attack'}
            on:click={() => (graphView = 'attack')}
          >
            Attack Graph
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={graphView === 'belief'}
            class="view-tab"
            class:active={graphView === 'belief'}
            on:click={() => (graphView = 'belief')}
          >
            Belief Map
          </button>
        </div>

        {#if graphView === 'attack'}
          <AttackGraph />
        {:else}
          <BeliefMap targetHash={$selectedTargetHash} />
        {/if}

        <button
          type="button"
          class="edge-toggle sidebar-toggle"
          class:open={sidebarOpen}
          on:click={() => sidebarOpen = !sidebarOpen}
          aria-label={sidebarOpen ? 'Collapse controls panel' : 'Expand controls panel'}
          aria-expanded={sidebarOpen}
          aria-controls="app-sidebar"
          title={sidebarOpen ? 'Collapse controls' : 'Open controls'}
        >
          <svg class="chevron" viewBox="0 0 16 16" width="11" height="11" aria-hidden="true" focusable="false">
            <polyline points="6,3 11,8 6,13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>

        <button
          type="button"
          class="edge-toggle bottom-toggle"
          class:open={bottomOpen}
          on:click={() => bottomOpen = !bottomOpen}
          aria-label={bottomOpen ? 'Collapse chat panel' : 'Expand chat panel'}
          aria-expanded={bottomOpen}
          aria-controls="app-bottom-panel"
          title={bottomOpen ? 'Collapse chat' : 'Open chat'}
        >
          <svg class="chevron" viewBox="0 0 16 16" width="11" height="11" aria-hidden="true" focusable="false">
            <polyline points="6,3 11,8 6,13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
      </div>

      <div
        id="app-bottom-panel"
        class="bottom-panel"
        class:open={bottomOpen}
        inert={!bottomOpen}
      >
        <div class="mode-bar">
          <div
            class="mode-pill"
            role="tablist"
            aria-label="Bottom-panel mode"
          >
            <button
              class="mode-btn"
              class:active={$mode === 'autonomous'}
              role="tab"
              aria-selected={$mode === 'autonomous'}
              title="Hide chat — show only activity log"
              on:click={() => $mode = 'autonomous'}
            >
              <span class="mb-ico">🤖</span>
              <span class="mb-lbl">Autonomous</span>
            </button>
            <button
              class="mode-btn"
              class:active={$mode === 'co-op'}
              role="tab"
              aria-selected={$mode === 'co-op'}
              title="Show chat — talk to the executive live"
              on:click={() => $mode = 'co-op'}
            >
              <span class="mb-ico">💬</span>
              <span class="mb-lbl">Chat</span>
            </button>
          </div>
        </div>
        <div class="bottom-split" class:chat-hidden={$mode !== 'co-op'}>
          {#if $mode === 'co-op'}
            <div class="bottom-chat">
              <CoPilotChat />
            </div>
          {/if}
          <div class="bottom-activity">
            <ActivityPanel />
          </div>
        </div>
      </div>
    </div>

    {#if $selectedNode}
      <NodeDetail />
    {/if}
  </main>

  {#if $modulesDrawerOpen}
    <div class="drawer-overlay" on:click={() => $modulesDrawerOpen = false} role="presentation"></div>
    <aside class="drawer">
      <div class="drawer-header">
        <div class="drawer-title">
          <span class="dot live" aria-hidden="true"></span>
          <span class="pixel-label">mesmer ▸ modules</span>
        </div>
        <button class="close-btn" on:click={() => $modulesDrawerOpen = false} aria-label="Close">&times;</button>
      </div>
      <ModuleBrowser />
    </aside>
  {/if}

  <ScratchpadDrawer />
</div>

{/if}

<style>
  .app {
    display: flex;
    height: 100vh;
    width: 100vw;
    overflow: hidden;
  }

  .main {
    flex: 1;
    display: flex;
    min-width: 0;
  }

  /* Sidebar host: always rendered, animates width 0 ↔ 280. The inner
     Sidebar keeps its 280px natural width; overflow:hidden on the host
     clips it during the open/close transition. Because the host's width
     drives the layout, graph-area's left edge slides smoothly with it. */
  .sidebar-host {
    flex-shrink: 0;
    display: flex;
    width: 0;
    overflow: hidden;
    transition: width 0.2s ease;
  }
  .sidebar-host.open {
    width: 280px;                /* must match Sidebar.svelte .sidebar width */
  }

  .center-area {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  /* Graph-area is the anchor frame for both edge toggles. As the sidebar
     opens its left edge slides right; as the bottom panel opens its
     bottom edge slides up. The toggles ride those edges automatically —
     no `left`/`bottom` transitions on the toggles themselves, so they
     stay pixel-perfect in sync with the panel animation, and the
     sidebar toggle's vertical center naturally sits *inside* the visible
     graph viewport (above the chat-panel tabs) when the bottom is open. */
  .graph-area {
    position: relative;
    flex: 1;
    display: flex;
    min-height: 0;
  }

  /* Graph view toggle (Session 3): pill horizontally centered above the
     canvas — anchors as the primary view-mode switch rather than
     fighting for the top-left with each view's own toolbar. Both
     backends run side-by-side; this toggle is purely a UI affordance. */
  .graph-view-toggle {
    position: absolute;
    top: 0.5rem;
    left: 50%;
    transform: translateX(-50%);
    z-index: 6;
    display: flex;
    gap: 0.25rem;
    background: color-mix(in srgb, var(--surface-2, #181818) 88%, transparent);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    border: 1px solid var(--border, #333);
    border-radius: 999px;
    padding: 2px;
  }
  .graph-view-toggle .view-tab {
    background: transparent;
    border: none;
    color: var(--text-muted);
    padding: 0.25rem 0.7rem;
    border-radius: 999px;
    cursor: pointer;
    font-size: 0.78rem;
    letter-spacing: 0.04em;
  }
  .graph-view-toggle .view-tab:hover {
    color: var(--text);
  }
  .graph-view-toggle .view-tab.active {
    color: var(--bg-secondary, #000);
    background: var(--phosphor);
  }

  /* Bottom panel: same width-trick on the vertical axis. */
  .bottom-panel {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    height: 0;
    overflow: hidden;
    background: var(--bg-secondary);
    transition: height 0.2s ease;
  }
  .bottom-panel.open {
    height: 392px;
    border-top: 1px solid var(--border);
  }

  /* Bottom-panel mode bar: a thin strip above the columns with a single
     two-state pill. Autonomous = activity-only (full width). Chat =
     two-column split (chat on left, activity on right). The mode IS the
     chat-on/off switch; one concept, no separate hide/show toggle. */
  .mode-bar {
    flex-shrink: 0;
    display: flex;
    justify-content: center;
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
    background: var(--bg-primary);
  }
  .mode-pill {
    display: inline-flex;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 2px;
    gap: 2px;
  }
  .mode-btn {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 12px;
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    font-weight: 400;
    border-radius: 3px;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    transition: background 120ms, color 120ms, box-shadow 120ms;
  }
  .mode-btn:hover { color: var(--text); }
  .mode-btn.active {
    background: var(--accent);
    color: #000;
    box-shadow: var(--button-glow);
  }
  .mode-btn .mb-ico { font-size: 0.85rem; line-height: 1; }
  .mode-btn .mb-lbl { font-family: var(--font-pixel); }

  /* Horizontal split inside the bottom panel. In autonomous, the chat
     column is gone and activity expands to full width. */
  .bottom-split {
    flex: 1;
    display: flex;
    min-height: 0;
  }
  .bottom-chat {
    flex: 1.4;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }
  .bottom-activity {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    border-left: 1px solid var(--border);
  }
  .bottom-split.chat-hidden .bottom-activity {
    border-left: none;
  }

  /* === Edge toggle (shared by both panel toggles) ============================
     Single primitive, two orientations. The chevron rotates to point the
     way the panel will open / close. Same shape, color, hover, focus
     treatment for both — they read as one design language. */
  .edge-toggle {
    position: absolute;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text-muted);
    cursor: pointer;
    padding: 0;
    z-index: 50;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: color 0.15s, border-color 0.15s, background 0.15s;
  }
  .edge-toggle:hover {
    color: var(--accent);
    border-color: var(--accent);
    background: var(--bg-secondary);
    box-shadow: var(--phosphor-glow-tight);
  }
  .edge-toggle:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .chevron {
    transition: transform 0.2s ease;
  }

  /* Sidebar toggle: vertical tab on graph-area's left edge.
     Vertically centered on the *graph-area* (not the viewport), so when
     the chat panel opens and shrinks the graph viewport, the toggle
     naturally slides up and stays clear of the PLAN/CO-OP/AUTONOMOUS
     tabs. No left/top transition needed — the parent reflows smoothly. */
  .sidebar-toggle {
    top: 50%;
    left: 0;
    width: 18px;
    height: 56px;
    margin-top: -28px;
    border-left: none;
    border-radius: 0 6px 6px 0;
  }
  .sidebar-toggle.open .chevron {
    transform: rotate(180deg);   /* › → ‹ */
  }

  /* Bottom-panel toggle: horizontal tab on graph-area's bottom edge. */
  .bottom-toggle {
    left: 50%;
    bottom: 0;
    width: 56px;
    height: 18px;
    margin-left: -28px;
    border-bottom: none;
    border-radius: 6px 6px 0 0;
  }
  .bottom-toggle .chevron {
    transform: rotate(-90deg);   /* › → ▴ (panel opens upward) */
  }
  .bottom-toggle.open .chevron {
    transform: rotate(90deg);    /* › → ▾ (panel closes downward) */
  }

  /* Drawer (modules) */
  .drawer-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 100;
    animation: fadeIn 0.15s ease-out;
  }

  .drawer {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    width: 480px;
    max-width: 90vw;
    background: var(--bg-secondary);
    border-left: 1px solid var(--border);
    z-index: 101;
    display: flex;
    flex-direction: column;
    animation: slideInRight 0.2s ease-out;
  }

  .drawer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-tertiary);
  }

  .drawer-title {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .drawer-title .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
    animation: phosphor-pulse 2s ease-in-out infinite;
  }

  @keyframes phosphor-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.55; }
  }

  .close-btn {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 1.6rem;
    cursor: pointer;
    line-height: 1;
    padding: 0 4px;
  }
  .close-btn:hover { color: var(--text); }

  @keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
  }

  @keyframes slideInRight {
    from { transform: translateX(100%); }
    to { transform: translateX(0); }
  }
</style>
