<script>
  import { createEventDispatcher, tick } from 'svelte'
  import { validateScenario } from '../lib/api.js'

  export let yamlContent = ''
  export let disabled = false

  const dispatch = createEventDispatcher()
  const INDENT = '  '

  let editorEl
  let gutterEl
  let validateTimer = null
  let validateState = { ok: true, error: null, checking: false }
  let lastScheduled = null
  let cursor = { line: 1, column: 1 }

  $: lines = yamlContent.split('\n')
  $: gutterWidth = `${Math.max(2, String(lines.length).length) + 1}ch`
  $: if (yamlContent !== lastScheduled) {
    lastScheduled = yamlContent
    scheduleValidate(yamlContent)
  }

  function emitChange(value) {
    yamlContent = value
    dispatch('change', value)
    updateCursor()
  }

  function onInput(event) {
    emitChange(event.currentTarget.value)
  }

  function onScroll() {
    if (!editorEl || !gutterEl) return
    gutterEl.scrollTop = editorEl.scrollTop
  }

  function updateCursor() {
    if (!editorEl) return
    const offset = editorEl.selectionStart ?? 0
    const before = yamlContent.slice(0, offset)
    const parts = before.split('\n')
    cursor = {
      line: parts.length,
      column: parts[parts.length - 1].length + 1,
    }
  }

  async function replaceSelection(nextValue, nextStart, nextEnd = nextStart) {
    emitChange(nextValue)
    await tick()
    editorEl?.setSelectionRange(nextStart, nextEnd)
    updateCursor()
  }

  async function indentSelection(event) {
    const start = editorEl.selectionStart
    const end = editorEl.selectionEnd
    const lineStart = yamlContent.lastIndexOf('\n', start - 1) + 1
    const selected = yamlContent.slice(lineStart, end)
    const indented = selected
      .split('\n')
      .map((line) => `${INDENT}${line}`)
      .join('\n')
    const next = yamlContent.slice(0, lineStart) + indented + yamlContent.slice(end)
    const delta = indented.length - selected.length
    event.preventDefault()
    await replaceSelection(next, start + INDENT.length, end + delta)
  }

  async function outdentSelection(event) {
    const start = editorEl.selectionStart
    const end = editorEl.selectionEnd
    const lineStart = yamlContent.lastIndexOf('\n', start - 1) + 1
    const selected = yamlContent.slice(lineStart, end)
    let firstDelta = 0
    const outdented = selected
      .split('\n')
      .map((line, index) => {
        if (line.startsWith(INDENT)) {
          if (index === 0) firstDelta = INDENT.length
          return line.slice(INDENT.length)
        }
        if (line.startsWith(' ')) {
          if (index === 0) firstDelta = 1
          return line.slice(1)
        }
        return line
      })
      .join('\n')
    const next = yamlContent.slice(0, lineStart) + outdented + yamlContent.slice(end)
    const delta = selected.length - outdented.length
    event.preventDefault()
    await replaceSelection(
      next,
      Math.max(lineStart, start - firstDelta),
      Math.max(lineStart, end - delta),
    )
  }

  async function onKeydown(event) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
      event.preventDefault()
      dispatch('save')
      return
    }
    if (event.key === 'Tab') {
      if (event.shiftKey) {
        await outdentSelection(event)
      } else {
        await indentSelection(event)
      }
    }
  }

  function scheduleValidate(value) {
    if (validateTimer) clearTimeout(validateTimer)
    validateState = { ...validateState, checking: true }
    validateTimer = setTimeout(async () => {
      try {
        const result = await validateScenario(value)
        validateState = { ok: result.ok, error: result.error, checking: false }
      } catch (e) {
        validateState = { ok: false, error: e.message, checking: false }
      }
    }, 400)
  }
</script>

<section class="editor-shell">
  <header class="editor-header">
    <div>
      <h2>YAML</h2>
      <span>{lines.length} lines · Ln {cursor.line}, Col {cursor.column}</span>
    </div>
    <div
      class="lint"
      class:lint-ok={validateState.ok && !validateState.checking}
      class:lint-bad={!validateState.ok}
      title={validateState.error || ''}
    >
      {#if validateState.checking}
        <span class="lint-dot"></span> checking
      {:else if validateState.ok}
        <span class="lint-dot"></span> valid
      {:else}
        <span class="lint-dot"></span> invalid
      {/if}
    </div>
  </header>

  {#if !validateState.ok && validateState.error}
    <div class="error-line">{validateState.error}</div>
  {/if}

  <div class="editor-frame" style={`--gutter-width: ${gutterWidth}`}>
    <div class="gutter" bind:this={gutterEl} aria-hidden="true">
      {#each lines as _, i}
        <span>{i + 1}</span>
      {/each}
    </div>
    <textarea
      bind:this={editorEl}
      class="yaml-input"
      value={yamlContent}
      disabled={disabled}
      spellcheck="false"
      autocomplete="off"
      autocapitalize="off"
      autocorrect="off"
      wrap="off"
      on:input={onInput}
      on:keydown={onKeydown}
      on:click={updateCursor}
      on:keyup={updateCursor}
      on:select={updateCursor}
      on:scroll={onScroll}
      aria-label="Scenario YAML editor"
    ></textarea>
  </div>
</section>

<style>
  .editor-shell {
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
  }

  .editor-header {
    min-height: 54px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 10px 18px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-primary);
  }
  .editor-header h2 {
    margin: 0;
    color: var(--accent);
    font-family: var(--font-pixel);
    font-size: 0.9rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .editor-header span {
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 0.68rem;
  }

  .lint {
    max-width: min(48vw, 460px);
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: 0.72rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .lint-ok { color: var(--phosphor); }
  .lint-bad { color: var(--red); }
  .lint-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: currentColor;
    box-shadow: 0 0 8px currentColor;
    flex: 0 0 auto;
  }

  .error-line {
    padding: 8px 18px;
    border-bottom: 1px solid color-mix(in srgb, var(--red) 30%, transparent);
    background: color-mix(in srgb, var(--red) 10%, var(--bg-primary));
    color: var(--red);
    font-family: var(--font-mono);
    font-size: 0.72rem;
    line-height: 1.4;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .editor-frame {
    min-height: 0;
    flex: 1;
    display: grid;
    grid-template-columns: var(--gutter-width) minmax(0, 1fr);
    background: var(--bg-secondary);
  }

  .gutter {
    overflow: hidden;
    padding: 18px 8px 18px 12px;
    border-right: 1px solid var(--border);
    background: color-mix(in srgb, var(--bg-primary) 72%, var(--bg-secondary));
    color: color-mix(in srgb, var(--text-muted) 70%, transparent);
    font-family: var(--font-mono);
    font-size: 0.84rem;
    line-height: 1.55;
    text-align: right;
    user-select: none;
  }
  .gutter span {
    display: block;
    height: 1.55em;
  }

  .yaml-input {
    width: 100%;
    height: 100%;
    min-width: 0;
    resize: none;
    border: 0;
    outline: none;
    padding: 18px 20px;
    background: transparent;
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 0.84rem;
    line-height: 1.55;
    tab-size: 2;
    white-space: pre;
    overflow: auto;
    caret-color: var(--accent);
  }
  .yaml-input:focus {
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent);
  }
  .yaml-input:disabled {
    opacity: 0.7;
    cursor: wait;
  }
</style>
