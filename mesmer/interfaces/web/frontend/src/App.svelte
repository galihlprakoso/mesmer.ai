<script>
  import { onMount, onDestroy } from 'svelte'
  import { connect, disconnect, onMessage } from './lib/ws.js'
  import { handleMessage, modulesDrawerOpen, selectedNode, isRunning } from './lib/stores.js'
  import Sidebar from './components/Sidebar.svelte'
  import AttackGraph from './components/AttackGraph.svelte'
  import NodeDetail from './components/NodeDetail.svelte'
  import ActivityPanel from './components/ActivityPanel.svelte'
  import CoPilotChat from './components/CoPilotChat.svelte'
  import ModuleBrowser from './components/ModuleBrowser.svelte'

  let unsubscribe

  onMount(() => {
    connect()
    unsubscribe = onMessage(handleMessage)
  })

  onDestroy(() => {
    if (unsubscribe) unsubscribe()
    disconnect()
  })
</script>

<div class="app">
  <Sidebar />

  <main class="main">
    <div class="center-area">
      <div class="graph-area">
        <AttackGraph />
      </div>

      <div class="bottom-panel">
        <CoPilotChat />
      </div>
    </div>

    {#if $selectedNode}
      <NodeDetail />
    {:else if $isRunning}
      <ActivityPanel />
    {/if}
  </main>

  {#if $modulesDrawerOpen}
    <div class="drawer-overlay" on:click={() => $modulesDrawerOpen = false} role="presentation"></div>
    <aside class="drawer">
      <div class="drawer-header">
        <h2>Modules</h2>
        <button class="close-btn" on:click={() => $modulesDrawerOpen = false} aria-label="Close">&times;</button>
      </div>
      <ModuleBrowser />
    </aside>
  {/if}
</div>

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

  .center-area {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  .graph-area {
    flex: 1;
    display: flex;
    min-height: 300px;
    border-bottom: 1px solid var(--border);
  }

  .bottom-panel {
    height: 360px;
    min-height: 240px;
    display: flex;
    flex-direction: column;
    background: var(--bg-secondary);
  }

  /* Drawer */
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
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
  }

  .drawer-header h2 {
    margin: 0;
    font-size: 1rem;
    font-weight: 600;
    color: var(--text);
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
