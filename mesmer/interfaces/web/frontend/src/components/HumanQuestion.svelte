<script>
  import { send } from '../lib/ws.js'
  import { pendingQuestion } from '../lib/stores.js'

  export let question  // { question_id, question, options, context, module }

  let answerText = ''
  let submitting = false

  function submitAnswer(answer) {
    if (submitting || !answer?.trim()) return
    submitting = true
    send({
      type: 'human_answer',
      question_id: question.question_id,
      answer: answer.trim(),
    })
    // Optimistic clear — the `human_answered` status event will also clear it.
    $pendingQuestion = null
    answerText = ''
    submitting = false
  }

  function onKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submitAnswer(answerText)
    }
  }

  function skip() {
    submitAnswer('(no answer)')
  }
</script>

<div class="question-card">
  <div class="q-header">
    <span class="q-badge">🤖 AGENT ASKS</span>
    {#if question.module}
      <span class="q-module">{question.module}</span>
    {/if}
  </div>

  <div class="q-text">{question.question}</div>

  {#if question.context}
    <div class="q-context">
      <span class="q-context-label">Context:</span> {question.context}
    </div>
  {/if}

  {#if question.options?.length > 0}
    <div class="options">
      {#each question.options as opt}
        <button class="option-btn" on:click={() => submitAnswer(opt)} disabled={submitting}>
          {opt}
        </button>
      {/each}
    </div>
  {/if}

  <div class="answer-row">
    <textarea
      bind:value={answerText}
      on:keydown={onKeydown}
      placeholder="Type your answer... (Enter to send)"
      rows="2"
      disabled={submitting}
      autofocus
    ></textarea>
    <div class="answer-actions">
      <button class="send-btn" on:click={() => submitAnswer(answerText)} disabled={!answerText.trim() || submitting}>
        Answer
      </button>
      <button class="skip-btn" on:click={skip} disabled={submitting} title="Skip — agent continues with own judgement">
        Skip
      </button>
    </div>
  </div>
</div>

<style>
  .question-card {
    margin: 0 12px 8px;
    padding: 12px 14px;
    background: var(--accent-dim);
    border: 1px solid var(--accent);
    border-radius: 8px;
    animation: slideIn 0.2s ease-out;
  }

  @keyframes slideIn {
    from { transform: translateY(6px); opacity: 0; }
    to   { transform: translateY(0); opacity: 1; }
  }

  .q-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }

  .q-badge {
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--accent);
    background: var(--bg-primary);
    padding: 2px 8px;
    border-radius: 4px;
  }

  .q-module {
    font-size: 0.7rem;
    color: var(--text-muted);
    font-family: monospace;
  }

  .q-text {
    font-size: 0.9rem;
    font-weight: 500;
    color: var(--text);
    margin-bottom: 8px;
    line-height: 1.4;
  }

  .q-context {
    font-size: 0.75rem;
    color: var(--text-muted);
    margin-bottom: 10px;
    padding: 6px 8px;
    background: var(--bg-primary);
    border-radius: 4px;
    border-left: 2px solid var(--text-muted);
  }

  .q-context-label {
    font-weight: 600;
    color: var(--text);
  }

  .options {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 10px;
  }

  .option-btn {
    padding: 6px 12px;
    background: var(--bg-tertiary);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 0.8rem;
    cursor: pointer;
    font-weight: 500;
  }

  .option-btn:hover:not(:disabled) {
    background: var(--accent);
    color: #000;
    border-color: var(--accent);
  }

  .option-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .answer-row {
    display: flex;
    gap: 8px;
  }

  textarea {
    flex: 1;
    padding: 8px;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text);
    font-size: 0.82rem;
    font-family: inherit;
    resize: none;
  }

  textarea:focus:not(:disabled) {
    outline: 1px solid var(--accent);
    border-color: var(--accent);
  }

  .answer-actions {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .send-btn, .skip-btn {
    padding: 6px 12px;
    border: none;
    border-radius: 4px;
    font-weight: 600;
    font-size: 0.75rem;
    cursor: pointer;
    min-width: 70px;
  }

  .send-btn {
    background: var(--accent);
    color: #000;
  }
  .send-btn:hover:not(:disabled) { background: var(--accent-hover); }
  .send-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  .skip-btn {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text-muted);
  }
  .skip-btn:hover:not(:disabled) {
    color: var(--text);
    border-color: var(--text-muted);
  }
  .skip-btn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
