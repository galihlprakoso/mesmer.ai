<script>
  import { modules } from '../lib/stores.js'

  let expandedModule = null
  let moduleDetail = null
  let loadingDetail = false

  // Build the leader → children tree from the flat module list.
  //   leaders   : modules whose `sub_modules` is non-empty (orchestrators)
  //   children  : a leader's own sub_modules, resolved against $modules
  //                so they carry full description/category metadata
  //   standalone: modules that are neither leaders nor referenced as a
  //                sub-module of any leader (typically: nothing today, but
  //                future utility modules would land here)
  $: ({ leaders, standalone, byName } = buildTree($modules))

  function buildTree(mods) {
    const byName = new Map(mods.map(m => [m.name, m]))
    const leaders = mods.filter(m => (m.sub_modules?.length ?? 0) > 0)
    // Sub-modules referenced by at least one leader (string set).
    const referenced = new Set()
    for (const l of leaders) {
      for (const s of (l.sub_modules || [])) referenced.add(s)
    }
    const standalone = mods.filter(m =>
      (m.sub_modules?.length ?? 0) === 0 && !referenced.has(m.name)
    )
    return { leaders, standalone, byName }
  }

  // Resolve a sub-module name → its registered config (or a stub if the
  // sub_modules list references something not in the registry).
  function resolve(name) {
    const m = byName.get(name)
    if (m) return m
    return { name, description: '(not in registry)', category: '', sub_modules: [] }
  }

  async function toggleModule(name) {
    if (expandedModule === name) {
      expandedModule = null
      moduleDetail = null
      return
    }
    expandedModule = name
    loadingDetail = true
    try {
      const res = await fetch(`/api/modules/${encodeURIComponent(name)}`)
      moduleDetail = await res.json()
    } catch (e) {
      moduleDetail = { error: e.message }
    } finally {
      loadingDetail = false
    }
  }
</script>

<div class="module-browser">
  {#if leaders.length === 0 && standalone.length === 0}
    <p class="empty">No modules registered.</p>
  {/if}

  {#each leaders as leader (leader.name)}
    <div class="leader-group">
      <div class="row leader" class:expanded={expandedModule === leader.name}>
        <button class="row-btn" on:click={() => toggleModule(leader.name)} type="button">
          <span class="role">leader</span>
          <span class="name">{leader.name}</span>
          <span class="desc">{leader.description}</span>
          <span class="sub-count">{leader.sub_modules.length} sub</span>
        </button>
        {#if expandedModule === leader.name}
          <div class="detail">
            {#if loadingDetail}
              <p class="loading">Loading…</p>
            {:else if moduleDetail}
              {#if moduleDetail.theory}
                <div class="detail-section">
                  <span class="detail-label">Theory</span>
                  <p>{moduleDetail.theory}</p>
                </div>
              {/if}
              {#if moduleDetail.system_prompt}
                <div class="detail-section">
                  <span class="detail-label">System Prompt</span>
                  <pre>{moduleDetail.system_prompt.slice(0, 500)}{moduleDetail.system_prompt.length > 500 ? '…' : ''}</pre>
                </div>
              {/if}
            {/if}
          </div>
        {/if}
      </div>

      <div class="children">
        {#each leader.sub_modules as subName (subName)}
          {@const child = resolve(subName)}
          <div class="row child" class:expanded={expandedModule === child.name}>
            <button class="row-btn" on:click={() => toggleModule(child.name)} type="button">
              <span class="role role-{child.category || 'other'}">{child.category || 'other'}</span>
              <span class="name">{child.name}</span>
              <span class="desc">{child.description}</span>
            </button>
            {#if expandedModule === child.name}
              <div class="detail">
                {#if loadingDetail}
                  <p class="loading">Loading…</p>
                {:else if moduleDetail}
                  {#if moduleDetail.theory}
                    <div class="detail-section">
                      <span class="detail-label">Theory</span>
                      <p>{moduleDetail.theory}</p>
                    </div>
                  {/if}
                  {#if moduleDetail.system_prompt}
                    <div class="detail-section">
                      <span class="detail-label">System Prompt</span>
                      <pre>{moduleDetail.system_prompt.slice(0, 500)}{moduleDetail.system_prompt.length > 500 ? '…' : ''}</pre>
                    </div>
                  {/if}
                {/if}
              </div>
            {/if}
          </div>
        {/each}
      </div>
    </div>
  {/each}

  {#if standalone.length > 0}
    <div class="section-head">Standalone modules</div>
    {#each standalone as mod (mod.name)}
      <div class="row" class:expanded={expandedModule === mod.name}>
        <button class="row-btn" on:click={() => toggleModule(mod.name)} type="button">
          <span class="role role-{mod.category || 'other'}">{mod.category || 'other'}</span>
          <span class="name">{mod.name}</span>
          <span class="desc">{mod.description}</span>
        </button>
        {#if expandedModule === mod.name}
          <div class="detail">
            {#if loadingDetail}
              <p class="loading">Loading…</p>
            {:else if moduleDetail}
              {#if moduleDetail.theory}
                <div class="detail-section">
                  <span class="detail-label">Theory</span>
                  <p>{moduleDetail.theory}</p>
                </div>
              {/if}
              {#if moduleDetail.system_prompt}
                <div class="detail-section">
                  <span class="detail-label">System Prompt</span>
                  <pre>{moduleDetail.system_prompt.slice(0, 500)}{moduleDetail.system_prompt.length > 500 ? '…' : ''}</pre>
                </div>
              {/if}
            {/if}
          </div>
        {/if}
      </div>
    {/each}
  {/if}
</div>

<style>
  .module-browser {
    flex: 1;
    padding: 12px 16px;
    overflow-y: auto;
  }

  .empty {
    color: var(--text-muted);
    font-size: 0.85rem;
    padding: 24px;
    text-align: center;
  }

  .leader-group {
    margin-bottom: 18px;
  }

  .children {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-top: 4px;
    padding-left: 16px;
    border-left: 1px solid var(--border);
    margin-left: 8px;
  }

  .section-head {
    font-family: var(--font-pixel);
    font-size: 0.6875rem;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    margin: 18px 0 8px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
  }

  .row {
    border: 1px solid var(--border);
    border-radius: 4px;
    overflow: hidden;
    margin-bottom: 4px;
    transition: border-color 120ms, box-shadow 120ms;
  }
  .row.expanded {
    border-color: var(--phosphor);
    box-shadow: var(--phosphor-glow-tight);
  }
  .row.leader {
    border-color: hsla(155 100% 42% / 0.5);
    background: var(--bg-tertiary);
  }

  .row-btn {
    width: 100%;
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 8px 12px;
    background: transparent;
    border: none;
    color: var(--text);
    cursor: pointer;
    text-align: left;
    font-size: 0.8rem;
  }
  .row-btn:hover { background: var(--bg-secondary); }

  .role {
    flex-shrink: 0;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 2px 6px;
    border-radius: 3px;
    background: var(--bg-primary);
    color: var(--text-muted);
    border: 1px solid var(--border);
    min-width: 56px;
    text-align: center;
  }
  .row.leader .role {
    color: var(--phosphor);
    border-color: hsla(155 100% 42% / 0.5);
    background: var(--accent-dim);
  }
  .role-techniques { color: var(--t2); border-color: var(--t2); }
  .role-planners   { color: var(--t1); border-color: var(--t1); }
  .role-profilers  { color: var(--t0); border-color: var(--t0); }

  .name {
    font-family: var(--font-mono);
    font-weight: 600;
    color: var(--phosphor);
    min-width: 150px;
    flex-shrink: 0;
  }
  .row.leader .name { color: var(--phosphor); text-shadow: var(--phosphor-glow); }
  .row.child .name { color: var(--text); }

  .desc {
    flex: 1;
    color: var(--text-muted);
    font-size: 0.75rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .sub-count {
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    background: var(--bg-primary);
    border: 1px solid var(--border);
    padding: 2px 6px;
    border-radius: 3px;
    flex-shrink: 0;
  }

  .detail {
    padding: 12px;
    border-top: 1px solid var(--border);
    background: var(--bg-secondary);
  }

  .detail-section { margin-bottom: 10px; }
  .detail-section:last-child { margin-bottom: 0; }

  .detail-label {
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    display: block;
    margin-bottom: 4px;
  }

  .detail-section p {
    margin: 0;
    font-size: 0.8rem;
    color: var(--text);
    line-height: 1.5;
  }

  pre {
    font-size: 0.72rem;
    color: var(--text-muted);
    white-space: pre-wrap;
    word-break: break-word;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    padding: 8px;
    border-radius: 4px;
    margin: 0;
    max-height: 200px;
    overflow-y: auto;
    font-family: var(--font-mono);
  }

  .loading {
    color: var(--text-muted);
    font-size: 0.8rem;
    margin: 0;
  }
</style>
