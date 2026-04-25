<script>
  import { onMount, createEventDispatcher } from 'svelte'
  import yaml from 'js-yaml'
  import { listModules, validateScenario } from '../lib/api.js'

  /** @type {string} The current YAML content. Two-way bound to parent. */
  export let yamlContent = ''
  /** @type {boolean} Disable inputs (e.g. while saving). */
  export let disabled = false

  const dispatch = createEventDispatcher()

  let activeTab = 'form' // 'form' | 'yaml'
  let modules = []
  let validateState = { ok: true, error: null, checking: false }
  let validateTimer = null

  // Friendly labels for the known categories. Anything unrecognized
  // ("private", custom user dirs) shows the raw folder name.
  const CATEGORY_LABELS = {
    attacks: 'Attacks (leader orchestrators)',
    planners: 'Planners',
    profilers: 'Profilers',
    techniques: 'Techniques',
  }
  // Display order — leaders first since they're the natural pick for
  // the scenario's `module:` field, then planners, profilers, techniques.
  const CATEGORY_ORDER = ['attacks', 'planners', 'profilers', 'techniques']

  $: groupedModules = groupByCategory(modules)

  function groupByCategory(mods) {
    const buckets = new Map()
    for (const m of mods) {
      const cat = m.category || 'other'
      if (!buckets.has(cat)) buckets.set(cat, [])
      buckets.get(cat).push(m)
    }
    // Sort entries inside each bucket by name for stable display.
    for (const list of buckets.values()) {
      list.sort((a, b) => a.name.localeCompare(b.name))
    }
    // Order the buckets: known categories first in CATEGORY_ORDER,
    // then the rest alphabetically.
    const known = CATEGORY_ORDER.filter(c => buckets.has(c))
    const rest = [...buckets.keys()].filter(c => !CATEGORY_ORDER.includes(c)).sort()
    return [...known, ...rest].map(cat => ({
      category: cat,
      label: CATEGORY_LABELS[cat] || cat,
      modules: buckets.get(cat),
    }))
  }

  // Form-bound state. Mirror of YAML — repopulated whenever yamlContent
  // changes from outside (loaded from server, edited via chat, etc).
  let form = blankForm()
  // Guard against feedback loops between form↔YAML conversion.
  let updatingYaml = false
  let updatingForm = false

  function blankForm() {
    return {
      name: '',
      description: '',
      target: {
        adapter: 'openai',
        base_url: '',
        url: '',
        model: '',
        api_key: '${OPENROUTER_API_KEY}',
        system_prompt: '',
      },
      objective: {
        goal: '',
        success_signals: '',
        max_turns: 25,
      },
      module: '',
      agent: {
        model: 'openrouter/anthropic/claude-sonnet-4-20250514',
        judge_model: '',
        api_key: '${OPENROUTER_API_KEY}',
        temperature: 0.7,
      },
    }
  }

  // YAML → form. Tolerant of missing keys; falls back to blankForm defaults.
  function yamlToForm(text) {
    try {
      const data = yaml.load(text) || {}
      const base = blankForm()
      base.name = data.name ?? ''
      base.description = data.description ?? ''
      const t = data.target || {}
      base.target.adapter = t.adapter ?? 'openai'
      base.target.base_url = t.base_url ?? ''
      base.target.url = t.url ?? ''
      base.target.model = t.model ?? ''
      base.target.api_key = t.api_key ?? base.target.api_key
      base.target.system_prompt = t.system_prompt ?? ''
      const o = data.objective || {}
      base.objective.goal = o.goal ?? ''
      base.objective.success_signals = Array.isArray(o.success_signals)
        ? o.success_signals.join('\n')
        : (o.success_signals ?? '')
      base.objective.max_turns = o.max_turns ?? 25
      base.module = data.module ?? ''
      const a = data.agent || {}
      base.agent.model = a.model ?? base.agent.model
      base.agent.judge_model = a.judge_model ?? ''
      base.agent.api_key = a.api_key ?? base.agent.api_key
      base.agent.temperature = a.temperature ?? 0.7
      return base
    } catch {
      return blankForm()
    }
  }

  function formToYaml(f) {
    const target = { adapter: f.target.adapter }
    if (f.target.base_url) target.base_url = f.target.base_url
    if (f.target.url) target.url = f.target.url
    if (f.target.model) target.model = f.target.model
    if (f.target.api_key) target.api_key = f.target.api_key
    if (f.target.system_prompt) target.system_prompt = f.target.system_prompt
    const objective = {
      goal: f.objective.goal,
      max_turns: Number(f.objective.max_turns) || 25,
    }
    const sig = (f.objective.success_signals || '').split('\n').map(s => s.trim()).filter(Boolean)
    if (sig.length) objective.success_signals = sig
    const agent = {
      model: f.agent.model,
    }
    if (f.agent.judge_model) agent.judge_model = f.agent.judge_model
    if (f.agent.api_key) agent.api_key = f.agent.api_key
    if (f.agent.temperature !== '' && f.agent.temperature != null) {
      agent.temperature = Number(f.agent.temperature)
    }
    const out = {
      name: f.name,
      description: f.description,
      target,
      objective,
      module: f.module,
      agent,
    }
    return yaml.dump(out, { lineWidth: 120, noRefs: true })
  }

  // Sync form ← yamlContent when yamlContent changes from outside.
  $: if (!updatingYaml) {
    updatingForm = true
    form = yamlToForm(yamlContent)
    queueMicrotask(() => { updatingForm = false })
  }

  function emitYaml(newYaml) {
    if (newYaml === yamlContent) return
    updatingYaml = true
    yamlContent = newYaml
    dispatch('change', newYaml)
    queueMicrotask(() => { updatingYaml = false })
    scheduleValidate(newYaml)
  }

  function onFormChange() {
    if (updatingForm) return
    emitYaml(formToYaml(form))
  }

  function onYamlInput(event) {
    emitYaml(event.target.value)
  }

  function scheduleValidate(value) {
    if (validateTimer) clearTimeout(validateTimer)
    validateState.checking = true
    validateTimer = setTimeout(async () => {
      try {
        const r = await validateScenario(value)
        validateState = { ok: r.ok, error: r.error, checking: false }
      } catch (e) {
        validateState = { ok: false, error: e.message, checking: false }
      }
    }, 500)
  }

  onMount(async () => {
    try {
      modules = await listModules()
    } catch (e) {
      console.error('Failed to load modules:', e)
    }
    scheduleValidate(yamlContent)
  })
</script>

<div class="form-host">
  <div class="tab-bar">
    <button
      type="button"
      class="tab"
      class:active={activeTab === 'form'}
      on:click={() => (activeTab = 'form')}
    >Form</button>
    <button
      type="button"
      class="tab"
      class:active={activeTab === 'yaml'}
      on:click={() => (activeTab = 'yaml')}
    >YAML</button>

    <div class="lint" class:lint-ok={validateState.ok && !validateState.checking} class:lint-bad={!validateState.ok}>
      {#if validateState.checking}
        <span class="lint-dot"></span> checking…
      {:else if validateState.ok}
        <span class="lint-dot"></span> valid
      {:else}
        <span class="lint-dot"></span> {validateState.error}
      {/if}
    </div>
  </div>

  <div class="tab-body">
    {#if activeTab === 'form'}
      <div class="form-grid">
        <section>
          <h4>Scenario</h4>
          <label>
            <span>Name</span>
            <input type="text" bind:value={form.name} on:input={onFormChange} {disabled} />
          </label>
          <label>
            <span>Description</span>
            <textarea rows="2" bind:value={form.description} on:input={onFormChange} {disabled}></textarea>
          </label>
        </section>

        <section>
          <h4>Target</h4>
          <label>
            <span>Adapter</span>
            <select bind:value={form.target.adapter} on:change={onFormChange} {disabled}>
              <option value="openai">openai</option>
              <option value="echo">echo</option>
              <option value="rest">rest</option>
              <option value="websocket">websocket</option>
            </select>
          </label>
          {#if form.target.adapter === 'openai'}
            <label>
              <span>Base URL</span>
              <input type="text" bind:value={form.target.base_url} on:input={onFormChange} placeholder="https://openrouter.ai/api/v1" {disabled} />
            </label>
            <label>
              <span>Model</span>
              <input type="text" bind:value={form.target.model} on:input={onFormChange} placeholder="openai/gpt-4o-mini" {disabled} />
            </label>
          {:else}
            <label>
              <span>URL</span>
              <input type="text" bind:value={form.target.url} on:input={onFormChange} {disabled} />
            </label>
            <label>
              <span>Model (optional)</span>
              <input type="text" bind:value={form.target.model} on:input={onFormChange} {disabled} />
            </label>
          {/if}
          <label>
            <span>API key (env-var placeholder)</span>
            <input type="text" bind:value={form.target.api_key} on:input={onFormChange} placeholder={'${OPENROUTER_API_KEY}'} {disabled} />
          </label>
          <label>
            <span>Target system prompt (optional)</span>
            <textarea rows="3" bind:value={form.target.system_prompt} on:input={onFormChange} {disabled}></textarea>
          </label>
        </section>

        <section>
          <h4>Objective</h4>
          <label>
            <span>Goal</span>
            <textarea rows="3" bind:value={form.objective.goal} on:input={onFormChange} {disabled}></textarea>
          </label>
          <label>
            <span>Success signals (one per line)</span>
            <textarea rows="3" bind:value={form.objective.success_signals} on:input={onFormChange} {disabled}></textarea>
          </label>
          <label>
            <span>Max turns</span>
            <input type="number" min="1" max="200" bind:value={form.objective.max_turns} on:input={onFormChange} {disabled} />
          </label>
        </section>

        <section>
          <h4>Leader module</h4>
          <label>
            <span>Module</span>
            <select bind:value={form.module} on:change={onFormChange} {disabled}>
              <option value="">— Select a module —</option>
              {#each groupedModules as group (group.category)}
                <optgroup label={group.label}>
                  {#each group.modules as m (m.name)}
                    <option value={m.name}>{m.name}</option>
                  {/each}
                </optgroup>
              {/each}
            </select>
          </label>
          {#if form.module}
            {@const meta = modules.find(m => m.name === form.module)}
            {#if meta}
              <p class="help">{meta.description}</p>
            {/if}
          {/if}
        </section>

        <section>
          <h4>Agent (attacker)</h4>
          <label>
            <span>Model</span>
            <input type="text" bind:value={form.agent.model} on:input={onFormChange} {disabled} />
          </label>
          <label>
            <span>Judge model (optional, falls back to model)</span>
            <input type="text" bind:value={form.agent.judge_model} on:input={onFormChange} {disabled} />
          </label>
          <label>
            <span>API key (env-var placeholder)</span>
            <input type="text" bind:value={form.agent.api_key} on:input={onFormChange} placeholder={'${OPENROUTER_API_KEY}'} {disabled} />
          </label>
          <label>
            <span>Temperature</span>
            <input type="number" step="0.1" min="0" max="2" bind:value={form.agent.temperature} on:input={onFormChange} {disabled} />
          </label>
        </section>
      </div>
    {:else}
      <textarea
        class="yaml-area"
        spellcheck="false"
        value={yamlContent}
        on:input={onYamlInput}
        {disabled}
      ></textarea>
    {/if}
  </div>
</div>

<style>
  .form-host {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
    overflow: hidden;
  }

  .tab-bar {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 12px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-primary);
    flex-shrink: 0;
  }
  .tab {
    background: transparent;
    border: none;
    color: var(--text-muted);
    padding: 10px 14px;
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .tab:hover { color: var(--text); }
  .tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  .lint {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 0.7rem;
    font-family: var(--mono);
    color: var(--text-muted);
    padding: 4px 8px;
    border-radius: 4px;
    max-width: 50%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .lint-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--text-muted);
  }
  .lint-ok { color: var(--green); }
  .lint-ok .lint-dot { background: var(--green); }
  .lint-bad { color: var(--red); }
  .lint-bad .lint-dot { background: var(--red); }

  .tab-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
  }

  .form-grid {
    display: flex;
    flex-direction: column;
    gap: 18px;
  }

  section {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--border);
  }
  section:last-child { border-bottom: none; padding-bottom: 0; }

  section h4 {
    margin: 0 0 4px 0;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-muted);
  }

  label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 0.75rem;
    color: var(--text-muted);
  }
  input, select, textarea {
    background: var(--bg-tertiary);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text);
    padding: 7px 9px;
    font-size: 0.85rem;
    font-family: inherit;
  }
  input:focus, select:focus, textarea:focus {
    outline: none;
    border-color: var(--accent);
  }
  textarea {
    resize: vertical;
    min-height: 60px;
    font-family: inherit;
  }

  .help {
    margin: 0;
    font-size: 0.72rem;
    color: var(--text-muted);
    line-height: 1.4;
  }

  .yaml-area {
    width: 100%;
    height: calc(100vh - 180px);
    min-height: 360px;
    font-family: var(--mono);
    font-size: 0.8rem;
    line-height: 1.55;
    background: var(--bg-tertiary);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 12px;
    resize: none;
  }
  .yaml-area:focus {
    outline: none;
    border-color: var(--accent);
  }
</style>
