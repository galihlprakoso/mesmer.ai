<script>
  import { modules } from '../lib/stores.js'

  let expandedModule = null
  let moduleDetail = null
  let loadingDetail = false

  async function toggleModule(name) {
    if (expandedModule === name) {
      expandedModule = null
      moduleDetail = null
      return
    }

    expandedModule = name
    loadingDetail = true

    try {
      const res = await fetch(`/api/modules/${name}`)
      moduleDetail = await res.json()
    } catch (e) {
      moduleDetail = { error: e.message }
    } finally {
      loadingDetail = false
    }
  }
</script>

<div class="module-browser">
  <div class="module-list">
    {#each $modules as mod}
      <div class="module-card" class:expanded={expandedModule === mod.name}>
        <button class="module-header" on:click={() => toggleModule(mod.name)}>
          <span class="module-name">{mod.name}</span>
          <span class="module-desc">{mod.description}</span>
          {#if mod.sub_modules?.length}
            <span class="sub-count">{mod.sub_modules.length} sub</span>
          {/if}
        </button>

        {#if expandedModule === mod.name}
          <div class="module-detail">
            {#if loadingDetail}
              <p class="loading">Loading...</p>
            {:else if moduleDetail}
              {#if moduleDetail.theory}
                <div class="detail-section">
                  <label>Theory</label>
                  <p>{moduleDetail.theory}</p>
                </div>
              {/if}
              {#if moduleDetail.sub_modules?.length}
                <div class="detail-section">
                  <label>Sub-modules</label>
                  <div class="sub-modules">
                    {#each moduleDetail.sub_modules as sub}
                      <span class="sub-badge">{sub}</span>
                    {/each}
                  </div>
                </div>
              {/if}
              {#if moduleDetail.system_prompt}
                <div class="detail-section">
                  <label>System Prompt</label>
                  <pre>{moduleDetail.system_prompt.slice(0, 500)}{moduleDetail.system_prompt.length > 500 ? '...' : ''}</pre>
                </div>
              {/if}
            {/if}
          </div>
        {/if}
      </div>
    {/each}
  </div>
</div>

<style>
  .module-browser {
    flex: 1;
    padding: 12px 16px;
    overflow-y: auto;
  }

  .module-list {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .module-card {
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }

  .module-card.expanded {
    border-color: var(--accent);
  }

  .module-header {
    width: 100%;
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 8px 12px;
    background: var(--bg-tertiary);
    border: none;
    color: var(--text);
    cursor: pointer;
    text-align: left;
    font-size: 0.8rem;
  }

  .module-header:hover { background: var(--bg-secondary); }

  .module-name {
    font-weight: 600;
    color: var(--accent);
    min-width: 150px;
  }

  .module-desc {
    flex: 1;
    color: var(--text-muted);
    font-size: 0.75rem;
  }

  .sub-count {
    font-size: 0.65rem;
    color: var(--text-muted);
    background: var(--bg-primary);
    padding: 2px 6px;
    border-radius: 4px;
  }

  .module-detail {
    padding: 12px;
    border-top: 1px solid var(--border);
  }

  .detail-section {
    margin-bottom: 10px;
  }

  .detail-section label {
    font-size: 0.65rem;
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

  .sub-modules {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .sub-badge {
    padding: 2px 8px;
    background: var(--accent-dim);
    color: var(--accent);
    border-radius: 4px;
    font-size: 0.7rem;
  }

  pre {
    font-size: 0.72rem;
    color: var(--text-muted);
    white-space: pre-wrap;
    word-break: break-word;
    background: var(--bg-primary);
    padding: 8px;
    border-radius: 4px;
    margin: 0;
    max-height: 200px;
    overflow-y: auto;
  }

  .loading {
    color: var(--text-muted);
    font-size: 0.8rem;
  }
</style>
