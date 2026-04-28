<script>
  import { marked } from 'marked'
  import { listArtifacts, loadScenario, readArtifact, searchArtifacts } from '../lib/api.js'

  export let targetHash = null
  export let scenarioPath = null
  export let version = 0

  marked.setOptions({
    gfm: true,
    breaks: false,
    smartLists: true,
  })

  let summaries = []
  let declaredArtifacts = []
  let visibleArtifacts = []
  let selectedId = null
  let selectedArtifact = null
  let query = ''
  let searchResults = []
  let loading = false
  let loadingDoc = false
  let error = ''
  let lastKey = ''

  $: loadKey = `${targetHash || ''}:${scenarioPath || ''}:${version}`
  $: if (targetHash && loadKey !== lastKey) {
    lastKey = loadKey
    loadSummaries()
  }
  $: visibleArtifacts = mergeDeclaredAndSaved(declaredArtifacts, summaries)

  async function loadSummaries() {
    if (!targetHash) return
    loading = true
    error = ''
    try {
      const [artifactData, scenarioData] = await Promise.all([
        listArtifacts(targetHash),
        scenarioPath ? loadScenario(scenarioPath).catch(() => ({ artifacts: [] })) : Promise.resolve({ artifacts: [] }),
      ])
      declaredArtifacts = scenarioData.artifacts || []
      const data = artifactData
      summaries = data.items || []
      const merged = mergeDeclaredAndSaved(declaredArtifacts, summaries)
      if (!selectedId || !merged.find(item => item.id === selectedId)) {
        selectedId = merged[0]?.id || null
      }
      if (selectedId) {
        await selectArtifact(selectedId)
      } else {
        selectedArtifact = null
      }
      if (query.trim()) {
        await runSearch()
      }
    } catch (e) {
      error = e.message || String(e)
      summaries = []
      selectedArtifact = null
    } finally {
      loading = false
    }
  }

  function mergeDeclaredAndSaved(declared, saved) {
    const savedById = new Map((saved || []).map(item => [item.id, item]))
    const out = (declared || []).map(spec => {
      const existing = savedById.get(spec.id)
      savedById.delete(spec.id)
      return {
        ...spec,
        ...(existing || {}),
        title: existing?.title || spec.title || spec.id,
        declared: true,
        empty: !existing,
      }
    })
    if ((declared || []).length) return out
    for (const item of savedById.values()) {
      out.push({ ...item, declared: false, empty: false })
    }
    return out
  }

  async function selectArtifact(id) {
    if (!targetHash || !id) return
    selectedId = id
    const declared = declaredArtifacts.find(item => item.id === id)
    loadingDoc = true
    error = ''
    try {
      selectedArtifact = await readArtifact(targetHash, id)
    } catch (e) {
      if (declared) {
        selectedArtifact = {
          artifact_id: declared.id,
          title: declared.title || declared.id,
          content: '',
          description: declared.description || '',
          empty: true,
        }
      } else {
        error = e.message || String(e)
        selectedArtifact = null
      }
    } finally {
      loadingDoc = false
    }
  }

  async function runSearch() {
    if (!targetHash || !query.trim()) {
      searchResults = []
      return
    }
    try {
      const data = await searchArtifacts(targetHash, query.trim(), 30)
      searchResults = data.items || []
    } catch (e) {
      error = e.message || String(e)
      searchResults = []
    }
  }

  function clearSearch() {
    query = ''
    searchResults = []
  }
</script>

<section class="artifacts-view">
  <aside class="artifact-list">
    <div class="panel-head">
      <div>
        <div class="eyebrow">Artifacts</div>
        <h2>Expected Outputs</h2>
      </div>
      <span class="count">{visibleArtifacts.length}</span>
    </div>

    <form class="search" on:submit|preventDefault={runSearch}>
      <input
        bind:value={query}
        placeholder="Search artifacts"
        aria-label="Search artifacts"
      />
      <button type="submit">Search</button>
      {#if query}
        <button type="button" class="ghost" on:click={clearSearch}>Clear</button>
      {/if}
    </form>

    {#if error}
      <div class="error">{error}</div>
    {/if}

    {#if query.trim() && searchResults.length}
      <div class="section-label">Search Results</div>
      <div class="results">
        {#each searchResults as hit}
          <button
            type="button"
            class="result"
            on:click={() => selectArtifact(hit.artifact_id)}
          >
            <span class="result-id">{hit.artifact_id}</span>
            <span class="result-heading">{hit.heading}</span>
            <span class="snippet">{hit.snippet}</span>
          </button>
        {/each}
      </div>
    {:else if query.trim() && !searchResults.length}
      <div class="empty small">No matches.</div>
    {/if}

    <div class="section-label">All Artifacts</div>
    {#if loading}
      <div class="empty small">Loading artifacts…</div>
    {:else if visibleArtifacts.length}
      <div class="items">
        {#each visibleArtifacts as artifact}
          <button
            type="button"
            class="artifact-item"
            class:selected={artifact.id === selectedId}
            on:click={() => selectArtifact(artifact.id)}
          >
            <span class="artifact-id">{artifact.id}</span>
            <span class="artifact-meta">
              {artifact.empty ? 'empty' : `${artifact.chars} chars`}
              {#if artifact.declared} · declared{/if}
            </span>
            {#if artifact.description}
              <span class="artifact-sections">{artifact.description}</span>
            {/if}
            {#if artifact.headings?.length}
              <span class="artifact-sections">{artifact.headings.slice(0, 3).join(' · ')}</span>
            {/if}
          </button>
        {/each}
      </div>
    {:else}
      <div class="empty">
        No declared artifacts yet. Add an `artifacts:` block to the scenario or ask the executive to save working notes.
      </div>
    {/if}
  </aside>

  <main class="artifact-doc">
    {#if loadingDoc}
      <div class="empty">Loading artifact…</div>
    {:else if selectedArtifact}
      <div class="doc-head">
        <div>
          <div class="eyebrow">{selectedArtifact.empty ? 'Declared Artifact' : 'Artifact'}</div>
          <h2>{selectedArtifact.artifact_id}</h2>
        </div>
        <span class="doc-size">{selectedArtifact.content.length} chars</span>
      </div>
      {#if selectedArtifact.empty}
        <div class="empty">
          {selectedArtifact.description || 'This artifact is declared by the scenario but has not been written yet.'}
        </div>
      {:else}
        <article class="md-render">
          {@html marked.parse(selectedArtifact.content || '')}
        </article>
      {/if}
    {:else}
      <div class="empty">Select an artifact to read it.</div>
    {/if}
  </main>
</section>

<style>
  .artifacts-view {
    display: grid;
    grid-template-columns: minmax(260px, 340px) minmax(0, 1fr);
    width: 100%;
    height: 100%;
    background: var(--bg-primary);
    color: var(--text);
  }

  .artifact-list {
    border-right: 1px solid var(--border);
    background: var(--bg-secondary);
    padding: 16px;
    overflow-y: auto;
  }

  .panel-head,
  .doc-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;
  }

  .eyebrow,
  .section-label {
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-muted);
  }

  h2 {
    margin: 4px 0 0;
    font-family: var(--font-mono);
    font-size: 16px;
    color: var(--phosphor);
    text-shadow: var(--phosphor-glow);
    word-break: break-word;
  }

  .count,
  .doc-size {
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 3px 7px;
    color: var(--text-muted);
    font-family: var(--mono);
    font-size: 10px;
    white-space: nowrap;
  }

  .search {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    gap: 6px;
    margin-bottom: 14px;
  }

  input {
    min-width: 0;
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    padding: 8px 9px;
    font-family: var(--mono);
    font-size: 12px;
  }

  button {
    border: 1px solid var(--border);
    background: var(--bg-tertiary);
    color: var(--text);
    border-radius: 4px;
    padding: 7px 9px;
    font-family: var(--font-pixel);
    font-size: 0.625rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
  }

  button:hover {
    border-color: var(--phosphor);
    color: var(--phosphor);
  }

  .ghost {
    color: var(--text-muted);
  }

  .section-label {
    margin: 14px 0 8px;
  }

  .items,
  .results {
    display: flex;
    flex-direction: column;
    gap: 7px;
  }

  .artifact-item,
  .result {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: 4px;
    width: 100%;
    text-align: left;
    text-transform: none;
    letter-spacing: 0;
    font-family: var(--mono);
    padding: 9px 10px;
  }

  .artifact-item.selected {
    border-color: var(--phosphor);
    background: hsla(155 100% 42% / 0.08);
    box-shadow: var(--phosphor-glow-tight);
  }

  .artifact-id,
  .result-id {
    color: var(--text);
    font-size: 12px;
    word-break: break-word;
  }

  .artifact-meta,
  .artifact-sections,
  .result-heading,
  .snippet {
    color: var(--text-muted);
    font-size: 11px;
    line-height: 1.4;
    word-break: break-word;
  }

  .snippet {
    color: var(--text);
  }

  .artifact-doc {
    min-width: 0;
    overflow-y: auto;
    padding: 22px 28px 44px;
  }

  .doc-head {
    border-bottom: 1px solid var(--border);
    padding-bottom: 14px;
  }

  .empty,
  .error {
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 12px;
    color: var(--text-muted);
    background: var(--bg-tertiary);
    font-size: 12px;
    line-height: 1.5;
  }

  .empty.small {
    padding: 8px 10px;
  }

  .error {
    border-color: var(--red);
    color: var(--red);
  }

  .md-render {
    max-width: 880px;
    font-size: 13px;
    line-height: 1.65;
    color: var(--text);
    word-break: break-word;
  }

  .md-render :global(h1),
  .md-render :global(h2) {
    color: var(--phosphor);
    font-family: var(--font-mono);
    margin: 18px 0 8px;
  }

  .md-render :global(h1:first-child),
  .md-render :global(h2:first-child) {
    margin-top: 0;
  }

  .md-render :global(h1) { font-size: 20px; }
  .md-render :global(h2) { font-size: 16px; }
  .md-render :global(h3) {
    font-size: 14px;
    margin: 16px 0 7px;
    font-family: var(--font-mono);
  }
  .md-render :global(p) { margin: 0 0 10px; }
  .md-render :global(ul),
  .md-render :global(ol) {
    margin: 0 0 12px;
    padding-left: 22px;
  }
  .md-render :global(li) { margin: 3px 0; }
  .md-render :global(code) {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 1px 5px;
    color: var(--phosphor);
  }
  .md-render :global(pre) {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 11px 12px;
    overflow-x: auto;
  }
  .md-render :global(pre code) {
    background: transparent;
    border: none;
    padding: 0;
    color: var(--text);
  }
  .md-render :global(blockquote) {
    margin: 8px 0 12px;
    padding: 4px 12px;
    border-left: 2px solid var(--cyan);
    color: var(--text-muted);
    background: rgba(255, 255, 255, 0.02);
  }
</style>
