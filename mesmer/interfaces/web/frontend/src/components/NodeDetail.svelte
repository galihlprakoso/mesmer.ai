<script>
  import { marked } from 'marked'
  import {
    selectedNode,
    moduleTiers,
    isRunning,
    selectedScenario,
    selectedTargetHash,
  } from '../lib/stores.js'

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

  function isSyntheticManager(node) {
    return !!node?._isSyntheticManager
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
  $: agentTrace = node?.agent_trace || []
  $: debugRef = node ? [
    `scenario=${$selectedScenario || ''}`,
    `target_hash=${$selectedTargetHash || ''}`,
    `run_id=${node.run_id || ''}`,
    `node_id=${node.id || ''}`,
  ].join('\n') : ''

  function plainText(markdown) {
    return (markdown || '')
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/^#{1,6}\s+/gm, '')
      .replace(/[*_~>#-]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
  }

  function firstLine(text) {
    return (text || '').split('\n').map(s => s.trim()).find(Boolean) || ''
  }

  function truncate(text, max = 180) {
    const clean = plainText(text)
    if (clean.length <= max) return clean
    return clean.slice(0, max - 1).trimEnd() + '…'
  }

  function outcomeTitle(n) {
    if (!n) return ''
    if (isSyntheticManager(n)) return 'Manager is running'
    const heading = firstLine(n.module_output || '')
    if (heading) return truncate(heading, 120)
    if (n.leaked_info) return truncate(n.leaked_info, 120)
    return truncate(n.approach, 120) || 'Attempt recorded'
  }

  function isArtifactOutput(n) {
    if (!n || n._isLeaderOrchestrator) return false
    if (n.status === 'completed') return true
    const hasOutput = !!(n.module_output || '').trim()
    const hasMessages = (n.messages_sent?.length ?? 0) || (n.target_responses?.length ?? 0)
    return hasOutput && !hasMessages && !n.leaked_info && !n.reflection
  }

  function displayStatus(n) {
    if (!n?.status) return ''
    if (isSyntheticManager(n)) return 'running'
    if (isArtifactOutput(n)) return 'output'
    return n.status
  }

  function scoreLabel(n) {
    if (!n || n._isLeaderOrchestrator || isSyntheticManager(n)) return ''
    if (isArtifactOutput(n)) return ''
    return Number.isFinite(n.score) ? `Score ${n.score}/10` : ''
  }

  function outputLabel(n) {
    if (n?.module === 'attack-planner') return 'Plan'
    return 'Output'
  }

  function traceLabel(event) {
    return String(event || '').replace(/_/g, ' ')
  }

  function tracePreview(item) {
    if (!item) return ''
    if (item.payload && Object.keys(item.payload).length) {
      return JSON.stringify(item.payload, null, 2)
    }
    return item.detail || ''
  }

  function traceMessages(item) {
    return item?.payload?.request?.messages || []
  }

  function traceTools(item) {
    return item?.payload?.request?.tools || []
  }

  function traceResponse(item) {
    return item?.payload?.response || {}
  }

  function traceUsage(item) {
    return item?.payload?.usage || {}
  }

  function messageContent(message) {
    const content = message?.content
    if (typeof content === 'string') return content
    if (content === undefined || content === null) return ''
    return JSON.stringify(content, null, 2)
  }

  function toolName(tool) {
    return tool?.function?.name || tool?.name || 'tool'
  }

  function toolDescription(tool) {
    return tool?.function?.description || tool?.description || ''
  }

  function toolParameters(tool) {
    return tool?.function?.parameters || tool?.parameters || null
  }

  function callArgs(call) {
    const raw = call?.function?.arguments
    if (!raw) return ''
    try {
      return JSON.stringify(JSON.parse(raw), null, 2)
    } catch {
      return raw
    }
  }

  function traceModel(item) {
    return item?.payload?.model || ''
  }

  function traceElapsed(item) {
    const elapsed = item?.payload?.elapsed_s
    return Number.isFinite(elapsed) ? `${elapsed}s` : ''
  }

  function pretty(value) {
    if (value === undefined || value === null) return ''
    if (typeof value === 'string') return value
    return JSON.stringify(value, null, 2)
  }

  function rawJson(value) {
    return JSON.stringify(value, null, 2)
  }

  function traceTime(ts) {
    if (!ts) return ''
    try {
      return new Date(ts * 1000).toLocaleTimeString()
    } catch {
      return ''
    }
  }

  async function copyDebugRef() {
    if (!debugRef) return
    try {
      await navigator.clipboard.writeText(debugRef)
    } catch {
      // Clipboard can be unavailable in non-secure browser contexts.
    }
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

    <!-- Always-visible context: banners + status badge live above the
         expandable detail sections
         because they're frame-of-reference, not content. -->
    <div class="banners">
      {#if node._isLeaderOrchestrator}
        <div class="leader-banner" class:running={$isRunning}>
          <b>{$isRunning ? 'LEADER · RUNNING' : 'LEADER · IDLE'}</b>
          The orchestrator module that picks which sub-module to delegate to
          and decides when the objective is met.
        </div>
      {/if}

      {#if !node._isLeaderOrchestrator && node.status}
        <div class="status-row">
          <span class="status-badge {displayStatus(node)}">{displayStatus(node)}</span>
          {#if scoreLabel(node)}
            <span class="score-pill">{scoreLabel(node)}</span>
          {/if}
        </div>
      {/if}
      {#if debugRef}
        <div class="debug-ref">
          <div class="debug-line">
            <span class="debug-key">Run</span>
            <span class="debug-value" title={node.run_id || ''}>{node.run_id || 'none'}</span>
            <button on:click={copyDebugRef} title="Copy scenario, target hash, run ID, and node ID">
              Copy Debug Ref
            </button>
          </div>
          <div class="debug-line">
            <span class="debug-key">Node</span>
            <span class="debug-value" title={node.id || ''}>{node.id || 'none'}</span>
          </div>
        </div>
      {/if}
    </div>

    <div class="detail-content">
      {#if !node._isLeaderOrchestrator}
        <section class="summary-card">
          <div class="summary-label">At a glance</div>
          <div class="summary-title">{outcomeTitle(node)}</div>
        </section>
      {/if}

      {#if node._isLeaderOrchestrator}
        {#if node._scenarioObjective}
          <details class="detail-section" open>
            <summary>Objective</summary>
            <pre>{node._scenarioObjective}</pre>
          </details>
        {/if}
      {/if}

      {#if agentTrace.length}
        <details class="detail-section" open>
          <summary>Agent Trace</summary>
          <div class="agent-trace">
            {#each agentTrace as item}
              <div class="trace-row {item.event}">
                <div class="trace-head">
                  <span class="trace-event">{traceLabel(item.event)}</span>
                  {#if item.iteration}
                    <span class="trace-meta">iter {item.iteration}</span>
                  {/if}
                  {#if item.actor}
                    <span class="trace-meta">{item.actor}</span>
                  {/if}
                  {#if traceTime(item.timestamp)}
                    <span class="trace-meta">{traceTime(item.timestamp)}</span>
                  {/if}
                </div>
                {#if item.event === 'llm_call'}
                  <div class="trace-summary">
                    {#if traceModel(item)}<span>{traceModel(item)}</span>{/if}
                    {#if traceElapsed(item)}<span>{traceElapsed(item)}</span>{/if}
                    {#if traceUsage(item).total_tokens !== undefined}
                      <span>{traceUsage(item).total_tokens} tok</span>
                    {/if}
                  </div>
                  <div class="trace-subtitle">Request messages</div>
                  {#each traceMessages(item) as message, i}
                    <div class="trace-message {message.role || 'message'}">
                      <div class="trace-message-head">
                        <span>{message.role || 'message'}</span>
                        <span>#{i + 1}</span>
                      </div>
                      <pre class="trace-prose">{messageContent(message)}</pre>
                      <details class="trace-raw">
                        <summary>Raw JSON</summary>
                        <pre class="trace-detail">{rawJson(message)}</pre>
                      </details>
                    </div>
                  {/each}
                  {#if traceTools(item).length}
                    <details class="trace-nested">
                      <summary>Tools exposed · {traceTools(item).length}</summary>
                      {#each traceTools(item) as tool}
                        <details class="trace-tool">
                          <summary>{toolName(tool)}</summary>
                          {#if toolDescription(tool)}
                            <div class="trace-tool-desc">{toolDescription(tool)}</div>
                          {/if}
                          {#if toolParameters(tool)}
                            <details class="trace-raw">
                              <summary>Parameters</summary>
                              <pre class="trace-detail">{pretty(toolParameters(tool))}</pre>
                            </details>
                          {/if}
                          <details class="trace-raw">
                            <summary>Raw JSON</summary>
                            <pre class="trace-detail">{rawJson(tool)}</pre>
                          </details>
                        </details>
                      {/each}
                    </details>
                  {/if}
                  <div class="trace-subtitle">Assistant response</div>
                  {#if traceResponse(item).content}
                    <div class="trace-message assistant">
                      <div class="trace-message-head"><span>assistant</span></div>
                      <pre class="trace-prose">{traceResponse(item).content}</pre>
                    </div>
                  {/if}
                  {#if traceResponse(item).tool_calls?.length}
                    {#each traceResponse(item).tool_calls as call}
                      <div class="trace-call-card">
                        <div class="trace-call-name">{call.function?.name || 'tool_call'}</div>
                        <pre class="trace-prose">{callArgs(call)}</pre>
                        <details class="trace-raw">
                          <summary>Raw JSON</summary>
                          <pre class="trace-detail">{rawJson(call)}</pre>
                        </details>
                      </div>
                    {/each}
                  {/if}
                  {#if item.payload?.usage}
                    <div class="trace-usage">
                      <span>prompt {traceUsage(item).prompt_tokens ?? 0}</span>
                      <span>completion {traceUsage(item).completion_tokens ?? 0}</span>
                      <span>total {traceUsage(item).total_tokens ?? 0}</span>
                    </div>
                    <details class="trace-raw">
                      <summary>Raw JSON</summary>
                      <pre class="trace-detail">{rawJson(item.payload)}</pre>
                    </details>
                  {/if}
                {:else if item.event === 'tool_call'}
                  <div class="trace-call-name">{item.payload?.name || item.detail}</div>
                  <div class="trace-subtitle">Arguments</div>
                  <pre class="trace-prose">{pretty(item.payload?.args)}</pre>
                  <div class="trace-subtitle">Result</div>
                  <pre class="trace-prose">{pretty(item.payload?.result)}</pre>
                  <details class="trace-raw">
                    <summary>Raw JSON</summary>
                    <pre class="trace-detail">{rawJson(item.payload)}</pre>
                  </details>
                {:else if tracePreview(item)}
                  <pre class="trace-detail">{tracePreview(item)}</pre>
                {/if}
              </div>
            {/each}
          </div>
        </details>
      {/if}

      {#if !node._isLeaderOrchestrator}
        {#if node.module_output}
          <details class="detail-section" open>
            <summary>{outputLabel(node)}</summary>
            <!-- module_output is the manager's / sub-module's concluded
                 write-up. Render structured markdown, but keep raw HTML
                 disabled because this content is LLM-generated. -->
            <div class="md-render">{@html marked.parse(node.module_output || '')}</div>
          </details>
        {/if}

        {#if node.leaked_info || node.reflection}
          <details class="detail-section">
            <summary>Judge Review</summary>
            {#if node.leaked_info}
              <div class="field-block">
                <span class="field-label">Extracted signal</span>
                <div class="leaked">{node.leaked_info}</div>
              </div>
            {/if}
            {#if node.reflection}
              <div class="field-block">
                <span class="field-label">Score rationale</span>
                <pre>{node.reflection}</pre>
              </div>
            {/if}
          </details>
        {/if}

        {#if node.approach || pairs.length}
          <details class="detail-section">
            <summary>Execution Trace</summary>
            {#if node.approach}
              <div class="field-block">
                <span class="field-label">Instruction</span>
                <pre>{node.approach}</pre>
              </div>
            {/if}
            {#if pairs.length}
              <div class="field-block">
                <span class="field-label">Target exchange</span>
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
              </div>
            {/if}
          </details>
        {/if}
      {/if}

      {#if moduleConfig}
        <details class="detail-section">
          <summary>Module Definition</summary>
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
        </details>
      {/if}
    </div>
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

  /* ---------- banners + status ---------- */
  .banners {
    padding: 10px 16px 0;
    flex-shrink: 0;
  }
  .status-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 4px 0 0 0;
    flex-wrap: wrap;
  }
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
  .status-badge.failed,
  .status-badge.blocked {
    background: rgba(239, 68, 68, 0.12);
    border-color: var(--red);
    color: var(--red);
  }
  .status-badge.pending,
  .status-badge.skipped {
    background: rgba(168, 162, 158, 0.10);
    border-color: var(--text-muted);
    color: var(--text);
  }
  .status-badge.completed,
  .status-badge.output {
    background: rgba(59, 130, 246, 0.12);
    border-color: var(--blue);
    color: var(--blue);
  }
  .status-badge.running {
    background: color-mix(in srgb, var(--amber) 14%, transparent);
    border-color: var(--amber);
    color: var(--amber);
  }
  .score-pill {
    display: inline-flex;
    align-items: center;
    padding: 4px 8px;
    border-radius: 3px;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    border: 1px solid var(--border);
    color: var(--text-muted);
    background: var(--bg-tertiary);
  }
  .debug-ref {
    margin-top: 8px;
    padding: 7px 8px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--bg-tertiary);
  }
  .debug-line {
    display: flex;
    align-items: center;
    gap: 7px;
    min-width: 0;
    margin-bottom: 5px;
  }
  .debug-line:last-child {
    margin-bottom: 0;
  }
  .debug-key {
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.5625rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    flex: 0 0 auto;
  }
  .debug-value {
    color: var(--text);
    font-family: var(--mono);
    font-size: 10px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    flex: 1;
  }
  .debug-ref button {
    flex: 0 0 auto;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.5625rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    padding: 3px 6px;
    cursor: pointer;
  }
  .debug-ref button:hover {
    border-color: var(--phosphor);
    color: var(--phosphor);
  }

  .detail-content {
    flex: 1;
    overflow-y: auto;
    padding: 12px 16px 24px;
    font-size: 12px;
  }

  .summary-card {
    background: hsla(155 100% 42% / 0.06);
    border: 1px solid hsla(155 100% 42% / 0.28);
    border-radius: 5px;
    padding: 9px 10px;
    margin-bottom: 10px;
  }
  .summary-label {
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 5px;
  }
  .summary-title {
    color: var(--text);
    font-size: 12px;
    line-height: 1.45;
    word-break: break-word;
  }
  .field-block {
    margin-bottom: 10px;
  }
  .field-block:last-child {
    margin-bottom: 0;
  }
  .field-label {
    display: block;
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    letter-spacing: 0.08em;
    margin-bottom: 5px;
    text-transform: uppercase;
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

  /* ---------- Markdown rendering for module output ----------
     Managers emit structured markdown reports (email-exfiltration-proof is
     the strongest proof dossier example, but tool-extraction /
     system-prompt-extraction also frequently emit headers + lists). The styles below give those
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

  /* ---------- progressive disclosure sections ---------- */
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

  .detail-section {
    margin-bottom: 8px;
  }
  .detail-section > summary {
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-muted);
    padding: 4px 0;
  }
  .detail-section[open] > summary {
    color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
    margin-bottom: 8px;
  }

  /* ---------- agent trace ---------- */
  .agent-trace {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .trace-row {
    border: 1px solid var(--border);
    border-left: 2px solid var(--text-muted);
    border-radius: 4px;
    background: rgba(255, 255, 255, 0.02);
    padding: 6px 7px;
  }
  .trace-row.llm_call {
    border-left-color: var(--cyan);
  }
  .trace-row.tool_call {
    border-left-color: var(--blue);
  }
  .trace-row.reasoning {
    border-left-color: #a78bfa;
  }
  .trace-row.conclude {
    border-left-color: var(--phosphor);
  }
  .trace-row.hard_stop,
  .trace-row.circuit_break {
    border-left-color: var(--red);
  }
  .trace-head {
    display: flex;
    align-items: center;
    gap: 5px;
    flex-wrap: wrap;
    margin-bottom: 5px;
  }
  .trace-event {
    color: var(--text);
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .trace-meta {
    color: var(--text-muted);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 1px 5px;
    font-family: var(--mono);
    font-size: 10px;
  }
  .trace-detail {
    border: none;
    background: transparent;
    padding: 0;
    margin: 0;
    max-height: 260px;
    overflow-y: auto;
  }
  .trace-detail.compact {
    color: var(--text-muted);
    max-height: none;
    margin-top: 6px;
  }
  .trace-summary,
  .trace-usage {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin: 4px 0 8px;
  }
  .trace-summary span,
  .trace-usage span {
    color: var(--text-muted);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 2px 6px;
    font-family: var(--mono);
    font-size: 10px;
  }
  .trace-subtitle {
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin: 8px 0 5px;
  }
  .trace-nested,
  .trace-tool {
    background: rgba(255, 255, 255, 0.015);
    margin: 5px 0;
    padding: 4px 6px;
  }
  .trace-nested > summary,
  .trace-tool > summary {
    color: var(--text-muted);
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0;
    text-transform: none;
  }
  .trace-message,
  .trace-call-card {
    border: 1px solid var(--border);
    border-radius: 4px;
    background: rgba(255, 255, 255, 0.018);
    padding: 7px 8px;
    margin: 5px 0;
  }
  .trace-message.system { border-left: 2px solid var(--phosphor); }
  .trace-message.user { border-left: 2px solid var(--cyan); }
  .trace-message.assistant { border-left: 2px solid var(--blue); }
  .trace-message.tool { border-left: 2px solid var(--amber); }
  .trace-message-head {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    color: var(--text-muted);
    font-family: var(--font-pixel);
    font-size: 0.5625rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 5px;
  }
  .trace-prose {
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
    max-height: 320px;
    overflow-y: auto;
    color: var(--text);
    line-height: 1.55;
  }
  .trace-call-name {
    color: var(--text);
    font-family: var(--mono);
    font-size: 12px;
    margin: 2px 0 6px;
  }
  .trace-tool-desc {
    color: var(--text);
    font-size: 11px;
    line-height: 1.45;
    margin: 4px 0 6px;
  }
  .trace-raw {
    margin-top: 6px;
    background: rgba(255, 255, 255, 0.012);
  }
  .trace-raw > summary {
    color: var(--text-muted);
    font-family: var(--mono);
    font-size: 10px;
    text-transform: none;
    letter-spacing: 0;
  }

  /* ---------- module definition content ---------- */
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
