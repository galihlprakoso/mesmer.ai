<script>
  import { marked } from 'marked'
  import { selectedNode, moduleTiers, isRunning } from '../lib/stores.js'

  // GFM tables, autolinks, sane line breaks. We don't enable raw HTML
  // because module_output is LLM-generated — sanitize-by-omission is
  // safer than allowing arbitrary tags through.
  marked.setOptions({
    gfm: true,
    breaks: false,
    smartLists: true,
  })

  // Cache full module configs fetched from /api/modules/{name}.
  const moduleCache = new Map()
  let moduleConfig = null
  let lastFetchedName = null

  function close() {
    selectedNode.set(null)
  }

  function tierOf(name) {
    return $moduleTiers[name] ?? 2
  }

  function isFrontier(node) {
    return node?.status === 'frontier'
  }

  async function fetchModuleConfig(name) {
    if (!name || name === 'leader' || name === 'root' || name === 'synthetic') return null
    // Synthesized executive (named `<scenario-stem>:executive`) is built
    // in-memory by runner.execute_run — never registered. The registry
    // GET would 404. Skip the round-trip.
    if (name.endsWith(':executive')) return null
    if (moduleCache.has(name)) return moduleCache.get(name)
    try {
      const res = await fetch(`/api/modules/${encodeURIComponent(name)}`)
      if (!res.ok) return null
      const cfg = await res.json()
      if (cfg.error) return null
      moduleCache.set(name, cfg)
      return cfg
    } catch {
      return null
    }
  }

  // Reactively reload config whenever the selected node's module changes.
  $: if ($selectedNode && $selectedNode.module && $selectedNode.module !== lastFetchedName) {
    lastFetchedName = $selectedNode.module
    moduleConfig = null
    fetchModuleConfig($selectedNode.module).then(cfg => {
      // Make sure the user hasn't navigated away while we were fetching.
      if ($selectedNode?.module === lastFetchedName) moduleConfig = cfg
    })
  } else if (!$selectedNode) {
    lastFetchedName = null
    moduleConfig = null
  }

  $: node = $selectedNode
  $: pairs = node && (node.messages_sent?.length || node.target_responses?.length)
    ? Array.from(
        { length: Math.max(node.messages_sent?.length ?? 0, node.target_responses?.length ?? 0) },
        (_, i) => ({ sent: node.messages_sent?.[i], got: node.target_responses?.[i] }),
      )
    : []

  // Tab list — derived from what data is actually present on this node.
  // Order matters: it's the default-pick precedence too (first tab wins).
  // Output goes FIRST when present because it's the deliverable — the
  // module's concluded write-up. For exploit-analysis specifically
  // it's a markdown findings report, but every manager / sub-module's
  // module_output is what the operator wants to read first; the
  // probe-level detail (approach, exchange) is drill-down.
  function computeTabs(n, p, cfg) {
    if (!n) return []
    const out = []
    if (n._isLeaderOrchestrator) {
      if (n._scenarioObjective) out.push({ key: 'objective', label: 'Objective' })
      if (cfg) out.push({ key: 'config', label: 'Config' })
      return out
    }
    if (n.module_output && !isFrontier(n)) out.push({ key: 'output', label: 'Output' })
    if (n.leaked_info && !isFrontier(n)) out.push({ key: 'leaks', label: 'Leaks' })
    if (n.approach) out.push({ key: 'approach', label: 'Approach' })
    if (p.length) out.push({ key: 'exchange', label: 'Exchange' })
    if (n.reflection && !isFrontier(n)) out.push({ key: 'reflection', label: 'Reflection' })
    if (cfg) out.push({ key: 'config', label: 'Config' })
    return out
  }

  let activeTab = null
  $: tabs = computeTabs(node, pairs, moduleConfig)
  // Default-pick / repair: when the tab list changes (different node, or
  // moduleConfig finished loading) and the current activeTab isn't in
  // the new list, jump to the first available.
  $: if (tabs.length > 0 && !tabs.find(t => t.key === activeTab)) {
    activeTab = tabs[0].key
  }
</script>

{#if node}
  <aside class="right-sidebar">
    <div class="header">
      {#if node._isLeaderOrchestrator}
        <h2 class="title leader">{node.module || 'leader'} <span class="leader-tag">· leader</span></h2>
      {:else}
        <h2 class="title">{node.module || node.id}</h2>
        {#if typeof node._seqNum === 'number'}
          <span class="seq-pill">#{node._seqNum}</span>
        {/if}
      {/if}
      <button class="close-btn" on:click={close} aria-label="Close">&times;</button>
    </div>

    <!-- Always-visible context: banners + status badge live ABOVE tabs
         because they're frame-of-reference, not content. -->
    <div class="banners">
      {#if node._isLeaderOrchestrator}
        <div class="leader-banner" class:running={$isRunning}>
          <b>{$isRunning ? 'LEADER · RUNNING' : 'LEADER · IDLE'}</b>
          The orchestrator module that picks which sub-module to delegate to
          and decides when the objective is met.
        </div>
      {:else if isFrontier(node)}
        <div class="frontier-banner">
          <b>FRONTIER · PROPOSED</b>
          A frontier proposal — suggested as a next move but not yet executed.
        </div>
      {/if}

      {#if !node._isLeaderOrchestrator && node.status}
        <div class="status-row">
          <span class="status-badge {node.status}">{node.status}</span>
        </div>
      {/if}
    </div>

    {#if tabs.length > 0}
      <div class="tab-strip" role="tablist" aria-label="Node detail sections">
        {#each tabs as t (t.key)}
          <button
            class="tab"
            class:active={activeTab === t.key}
            role="tab"
            aria-selected={activeTab === t.key}
            on:click={() => activeTab = t.key}
          >{t.label}</button>
        {/each}
      </div>

      <div class="tab-content">
        {#if activeTab === 'objective'}
          <pre>{node._scenarioObjective}</pre>

        {:else if activeTab === 'approach'}
          <pre>{node.approach}</pre>

        {:else if activeTab === 'exchange'}
          {#each pairs as p, i}
            {#if p.sent}
              <div class="msg me">
                <span class="lbl">attacker → target · turn {i + 1}</span>
                {p.sent}
              </div>
            {/if}
            {#if p.got}
              <div class="msg them">
                <span class="lbl">target → attacker</span>
                {p.got}
              </div>
            {/if}
          {/each}

        {:else if activeTab === 'leaks'}
          <div class="leaked">{node.leaked_info}</div>

        {:else if activeTab === 'reflection'}
          <pre>{node.reflection}</pre>

        {:else if activeTab === 'output'}
          <!-- module_output is the manager's / sub-module's concluded
               write-up. Many recent modules (exploit-analysis,
               tool-extraction reports) emit structured markdown
               with headings, lists, code blocks, tables — render
               those richly. We DON'T enable raw-HTML passthrough
               because the content is LLM-generated; marked drops
               unknown tags safely by default. -->
          <div class="md-render">{@html marked.parse(node.module_output || '')}</div>

        {:else if activeTab === 'config' && moduleConfig}
          <div class="config-tag">
            {moduleConfig.name} · T{moduleConfig.tier ?? tierOf(moduleConfig.name)}
          </div>
          <details open>
            <summary>description</summary>
            <pre>{moduleConfig.description}</pre>
          </details>
          {#if moduleConfig.theory}
            <details>
              <summary>theory</summary>
              <pre>{moduleConfig.theory}</pre>
            </details>
          {/if}
          {#if moduleConfig.system_prompt}
            <details>
              <summary>system_prompt ({moduleConfig.system_prompt.length} chars)</summary>
              <pre>{moduleConfig.system_prompt}</pre>
            </details>
          {/if}
          {#if moduleConfig.sub_modules?.length}
            <div class="sub-modules-row">
              <span class="sub-modules-key">sub_modules</span>
              <span class="sub-modules-val">{moduleConfig.sub_modules.join(', ')}</span>
            </div>
          {/if}
        {/if}
      </div>
    {/if}
  </aside>
{/if}

<style>
  .right-sidebar {
    width: 380px;
    min-width: 380px;
    background: var(--bg-secondary);
    border-left: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    animation: slideIn 0.2s ease-out;
  }
  @keyframes slideIn {
    from { transform: translateX(40px); opacity: 0; }
    to { transform: translateX(0); opacity: 1; }
  }

  .header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 14px 16px 8px;
    border-bottom: 1px solid var(--border);
  }
  .title {
    margin: 0;
    font-size: 14px;
    font-family: var(--font-mono);
    color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
    flex: 1;
    word-break: break-word;
  }
  .title.leader { color: var(--phosphor); font-style: italic; }
  .seq-pill {
    background: var(--bg-tertiary);
    color: var(--text-muted);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 1px 8px;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .close-btn {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 1.4rem;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
  }
  .close-btn:hover { color: var(--text); }

  /* ---------- banners + status (above tabs) ---------- */
  .banners {
    padding: 10px 16px 0;
    flex-shrink: 0;
  }
  .status-row { margin: 4px 0 0 0; }
  .status-badge {
    display: inline-flex;
    align-items: center;
    padding: 4px 11px;
    border-radius: 3px;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    border: 1px solid var(--border);
    background: var(--bg-tertiary);
    color: var(--text-muted);
  }
  .status-badge.promising {
    background: hsla(155 100% 42% / 0.12);
    border-color: var(--phosphor);
    color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
  }
  .status-badge.dead {
    background: rgba(239, 68, 68, 0.12);
    border-color: var(--red);
    color: var(--red);
  }
  .status-badge.alive {
    background: rgba(168, 162, 158, 0.10);
    border-color: var(--text-muted);
    color: var(--text);
  }
  .status-badge.frontier {
    background: transparent;
    border: 1px dashed var(--text-muted);
    color: var(--text-muted);
  }

  /* ---------- tab strip ---------- */
  .tab-strip {
    flex-shrink: 0;
    display: flex;
    gap: 2px;
    margin: 12px 16px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
    scrollbar-width: none;
  }
  .tab-strip::-webkit-scrollbar { display: none; }
  .tab {
    flex-shrink: 0;
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    font-weight: 400;
    padding: 6px 11px;
    cursor: pointer;
    border-radius: 3px 3px 0 0;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;
    transition: color 120ms, border-color 120ms;
  }
  .tab:hover { color: var(--text); }
  .tab.active {
    color: var(--phosphor);
    border-bottom-color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
  }

  /* ---------- tab content ---------- */
  .tab-content {
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px 24px;
    font-size: 12px;
  }

  pre {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    padding: 8px 10px;
    border-radius: 4px;
    font-family: var(--mono);
    font-size: 11px;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0 0 8px 0;
    color: var(--text);
    line-height: 1.55;
  }

  /* ---------- Markdown rendering for the Output tab ----------
     Modules emit structured markdown reports (exploit-analysis is the
     canonical example, but tool-extraction / system-prompt-extraction
     also frequently emit headers + lists). The styles below give those
     reports a comfortable read against the panel's narrow width. */
  .md-render {
    font-size: 12px;
    line-height: 1.55;
    color: var(--text);
    word-break: break-word;
  }
  .md-render :global(h1) {
    font-size: 14px;
    font-family: var(--mono);
    color: var(--phosphor);
    margin: 0 0 8px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 4px;
  }
  .md-render :global(h2) {
    font-size: 13px;
    font-family: var(--mono);
    color: var(--text);
    margin: 14px 0 6px;
  }
  .md-render :global(h3) {
    font-size: 12px;
    font-family: var(--mono);
    color: var(--text);
    margin: 10px 0 4px;
    text-transform: none;
    letter-spacing: 0;
  }
  .md-render :global(h4),
  .md-render :global(h5),
  .md-render :global(h6) {
    font-size: 12px;
    font-family: var(--mono);
    color: var(--text-muted);
    margin: 8px 0 4px;
  }
  .md-render :global(p) {
    margin: 0 0 8px;
  }
  .md-render :global(ul),
  .md-render :global(ol) {
    margin: 0 0 10px;
    padding-left: 18px;
  }
  .md-render :global(li) {
    margin: 2px 0;
  }
  .md-render :global(li > ul),
  .md-render :global(li > ol) {
    margin: 2px 0 2px;
  }
  .md-render :global(strong) {
    color: var(--text);
    font-weight: 600;
  }
  .md-render :global(em) {
    color: var(--text-muted);
    font-style: italic;
  }
  .md-render :global(a) {
    color: var(--cyan);
    text-decoration: underline;
  }
  .md-render :global(code) {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    padding: 1px 5px;
    border-radius: 3px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--phosphor);
  }
  .md-render :global(pre) {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    padding: 8px 10px;
    border-radius: 4px;
    margin: 0 0 10px;
    overflow-x: auto;
  }
  .md-render :global(pre code) {
    background: transparent;
    border: none;
    padding: 0;
    font-size: 11px;
    color: var(--text);
  }
  .md-render :global(blockquote) {
    border-left: 2px solid var(--cyan);
    padding: 2px 10px;
    margin: 6px 0 10px;
    color: var(--text-muted);
    background: rgba(255, 255, 255, 0.02);
  }
  .md-render :global(table) {
    border-collapse: collapse;
    margin: 8px 0 12px;
    font-family: var(--mono);
    font-size: 11px;
    width: 100%;
  }
  .md-render :global(th),
  .md-render :global(td) {
    border: 1px solid var(--border);
    padding: 4px 8px;
    text-align: left;
    vertical-align: top;
  }
  .md-render :global(th) {
    background: var(--bg-tertiary);
    color: var(--text);
    font-weight: 600;
  }
  .md-render :global(hr) {
    border: 0;
    border-top: 1px dashed var(--border);
    margin: 14px 0;
  }
  /* First child shouldn't push down with a top margin — it'd add
     visual whitespace above the rendered report. */
  .md-render > :global(*:first-child) {
    margin-top: 0;
  }

  /* ---------- nested config <details> (still useful inside Config tab) */
  details {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 4px 8px;
    margin-bottom: 6px;
  }
  details > summary {
    cursor: pointer;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-muted);
    padding: 2px 0;
    list-style: none;
  }
  details > summary::marker { display: none; }
  details > summary::-webkit-details-marker { display: none; }
  details > summary::before {
    content: '▸ ';
    display: inline-block;
    transition: transform 120ms;
  }
  details[open] > summary::before { content: '▾ '; }
  details > summary:hover { color: var(--accent); }
  details[open] > summary { color: var(--text); }
  details > pre {
    margin-top: 6px;
    border: none;
    padding: 0;
    background: transparent;
    max-height: 360px;
    overflow-y: auto;
  }

  /* ---------- config tab content ---------- */
  .config-tag {
    display: inline-block;
    font-family: var(--mono);
    font-size: 10px;
    color: var(--text-muted);
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 2px 6px;
    margin-bottom: 8px;
    letter-spacing: 0.04em;
  }
  .sub-modules-row {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin-top: 6px;
    padding: 6px 0 0 0;
    border-top: 1px solid var(--border);
    color: var(--text-muted);
    font-size: 11px;
  }
  .sub-modules-key {
    font-family: var(--mono);
  }
  .sub-modules-val {
    color: var(--text);
    font-family: var(--mono);
    max-width: 60%;
    text-align: right;
    word-break: break-all;
  }

  /* ---------- exchange messages ---------- */
  .msg {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 7px 9px;
    font-family: var(--mono);
    font-size: 11px;
    margin-bottom: 6px;
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.55;
    color: var(--text);
  }
  .msg.me { border-left: 2px solid var(--cyan); }
  .msg.them { border-left: 2px solid #a78bfa; }
  .msg .lbl {
    display: block;
    color: var(--text-muted);
    font-size: 10px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-bottom: 4px;
  }

  /* ---------- leaked highlight ---------- */
  .leaked {
    background: hsla(155 100% 42% / 0.08);
    border: 1px solid var(--phosphor);
    color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
    padding: 8px 10px;
    border-radius: 4px;
    font-family: var(--font-mono);
    font-size: 11px;
    word-break: break-word;
    white-space: pre-wrap;
    line-height: 1.55;
    box-shadow: var(--phosphor-glow-tight);
  }

  /* ---------- banners ---------- */
  .frontier-banner {
    background: rgba(245, 158, 11, 0.08);
    border: 1px solid var(--amber);
    color: #fde68a;
    padding: 8px 10px;
    border-radius: 4px;
    font-size: 11px;
    line-height: 1.55;
  }
  .frontier-banner b {
    display: block;
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    color: var(--amber);
    margin-bottom: 4px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-weight: 400;
  }
  .leader-banner {
    background: hsla(155 100% 42% / 0.08);
    border: 1px solid hsla(155 100% 42% / 0.5);
    color: var(--text);
    padding: 8px 10px;
    border-radius: 4px;
    font-size: 11px;
    line-height: 1.55;
  }
  .leader-banner.running {
    background: hsla(155 100% 42% / 0.12);
    border-color: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
  }
  .leader-banner b {
    display: block;
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
    margin-bottom: 4px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-weight: 400;
  }
  .leader-banner.running b { color: var(--phosphor); }

  .leader-tag {
    color: var(--text-muted);
    font-style: italic;
    font-weight: normal;
    font-size: 12px;
  }
</style>
