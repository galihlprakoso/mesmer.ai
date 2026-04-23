# Mesmer — Core Agent Loop

How `mesmer/core/agent/engine.py::run_react_loop` actually runs.

The cycle is **Plan → Execute → Judge → Reflect → Update**, universal for every module (YAML leader, Python custom, or nested sub-module). Each iteration: compress-if-needed → LLM call → tool dispatch → loop.

---

## Top-level flowchart

```mermaid
flowchart TD
  START([run_react_loop<br/>module, ctx, instruction]) --> BUILD_TOOLS["build_tool_list module, ctx<br/>• sub_modules as tools<br/>• send_message.SCHEMA<br/>• conclude.SCHEMA<br/>• ask_human.SCHEMA if CO_OP"]
  BUILD_TOOLS --> BUILD_PROMPT["Build initial messages<br/>system: CONTINUATION_PREAMBLE? + module.system_prompt<br/>user: instruction + objective + plan<br/>       + _build_graph_context frontier/dead-ends<br/>       + prior turns / fresh-session intel<br/>       + module_log + _budget_banner"]
  BUILD_PROMPT --> LOOP_HEAD{iteration &lt; max_iterations?}

  LOOP_HEAD -- no --> MAX_OUT([return 'Max iterations reached'])
  LOOP_HEAD -- yes --> COMPRESS{scenario_mode ==<br/>CONTINUOUS?}
  COMPRESS -- yes --> MC[maybe_compress ctx, messages<br/>summary-buffer if token cap exceeded]
  COMPRESS -- no --> CALL
  MC --> CALL

  CALL[_completion_with_retry ctx, messages, tools]
  CALL -->|None — all retries exhausted| LLM_ERR([return 'LLM error: all retries exhausted'])
  CALL -->|response| APPEND[messages.append _serialize_message msg]

  APPEND --> HAS_TOOLS{msg.tool_calls?}

  %% ── Reasoning path ────────────────────────────────
  HAS_TOOLS -- no --> REASON[consecutive_reasoning += 1<br/>log REASONING]
  REASON --> HARD{n ≥ 2·MAX_CONSEC?}
  HARD -- yes --> HARD_OUT([HARD_STOP: return refusal string])
  HARD -- no --> CIRC{n ≥ MAX_CONSEC?}
  CIRC -- yes --> NUDGE[append CIRCUIT_BREAK nudge:<br/>'pick a tool or conclude']
  NUDGE --> LOOP_HEAD
  CIRC -- no --> CONT[append 'Continue. next move?']
  CONT --> LOOP_HEAD

  %% ── Tool path ─────────────────────────────────────
  HAS_TOOLS -- yes --> ZERO[consecutive_reasoning = 0<br/>log TOOL_CALLS]
  ZERO --> FOR_CALL[for call in msg.tool_calls:<br/>args = _parse_args call.function.arguments]
  FOR_CALL --> IS_CONCLUDE{fn_name ==<br/>ToolName.CONCLUDE?}
  IS_CONCLUDE -- yes --> CONCLUDE_OUT([log CONCLUDE<br/>return args.result])
  IS_CONCLUDE -- no --> DISPATCH["dispatch_tool_call fn_name, …"]
  DISPATCH --> APPEND_TOOL[messages.append tool_result]
  APPEND_TOOL --> FOR_CALL
  FOR_CALL -->|all calls processed| LOOP_HEAD

  classDef exit fill:#2c3e50,color:#fff,stroke:#1b2631
  classDef io fill:#1f618d,color:#fff,stroke:#154360
  classDef decision fill:#b9770e,color:#fff,stroke:#7e5109
  class MAX_OUT,LLM_ERR,HARD_OUT,CONCLUDE_OUT exit
  class CALL,MC,DISPATCH,APPEND_TOOL io
  class COMPRESS,HAS_TOOLS,HARD,CIRC,IS_CONCLUDE,LOOP_HEAD decision
```

---

## `dispatch_tool_call` — what each tool does

`mesmer/core/agent/tools/__init__.py` routes by `ToolName`. `conclude` never reaches here — the engine short-circuits upstream.

```mermaid
flowchart TD
  IN([dispatch_tool_call fn_name]) --> RESOLVE["try: name = ToolName fn_name<br/>except ValueError: name = None"]
  RESOLVE --> BUILTIN{name in<br/>_BUILTIN_HANDLERS?}
  BUILTIN -- SEND_MESSAGE --> SM["send_message.handle<br/>→ ctx.send text → target.send<br/>→ append _budget_suffix +<br/>   pipeline-error flag if any"]
  BUILTIN -- ASK_HUMAN --> AH["ask_human.handle<br/>→ ctx.human_broker.create_question<br/>→ await Future<br/>→ tool_result answer"]
  BUILTIN -- no --> SUB_CHECK{fn_name in<br/>ctx.registry?}
  SUB_CHECK -- yes --> SUBMOD["sub_module.handle<br/>→ Plan·Execute·Judge·Reflect·Update<br/>see subgraph below"]
  SUB_CHECK -- no --> UNK["tool_result call.id,<br/>'Unknown tool: {fn_name}'"]

  SM --> OUT([tool_result dict])
  AH --> OUT
  SUBMOD --> OUT
  UNK --> OUT
```

---

## Sub-module delegation — the full cycle

`mesmer/core/agent/tools/sub_module.py::handle`. This is where the agent's attack-graph intelligence lives.

```mermaid
flowchart TD
  DELEG([sub_module.handle fn_name, args]) --> MISSED[_find_missed_frontier<br/>leader skipped frontier_id?]
  MISSED --> SNAP[turns_before = len ctx.turns]
  SNAP --> EXEC["PLAN + EXECUTE<br/>ctx.run_module fn_name, sub_instruction<br/>→ nested run_react_loop<br/>→ returns result str"]
  EXEC --> SUBTURNS[sub_turns = ctx.turns turns_before:]
  SUBTURNS --> JUDGE["JUDGE<br/>_judge_module_result ctx, …<br/>→ evaluate_attempt LLM role=JUDGE<br/>→ JudgeResult score 1-10, leaked,<br/>   promising_angle, dead_end, suggested_next"]
  JUDGE --> UPDATE["UPDATE<br/>_update_graph ctx, …<br/>• frontier_id? fulfill_frontier<br/>• else add_node under parent<br/>• auto-classify ALIVE / PROMISING / DEAD"]
  UPDATE --> REFLECT{"current_node &&<br/>judge_result?"}
  REFLECT -- yes --> EXPAND["REFLECT + expand frontier<br/>_reflect_and_expand<br/>• graph.propose_frontier top_k=3<br/>• refine_approach LLM writes opener<br/>• graph.add_frontier_node each<br/>• scoped to module.sub_modules"]
  REFLECT -- no --> SKIP[skip]
  EXPAND --> BUILD_RESULT
  SKIP --> BUILD_RESULT

  BUILD_RESULT[Build tool_result content:<br/>result + judge_info + missed-frontier nudge]
  BUILD_RESULT --> RET([return tool_result dict])

  classDef react fill:#117a65,color:#fff,stroke:#0b5345
  classDef graph fill:#6c3483,color:#fff,stroke:#4a235a
  class EXEC react
  class JUDGE react
  class UPDATE graph
  class EXPAND graph
```

---

## `_completion_with_retry` — LLM call with key rotation

`mesmer/core/agent/retry.py`. Rate-limits cool down keys and rotate; they don't sleep.

```mermaid
flowchart TD
  ENTRY([_completion_with_retry]) --> ATTEMPT{attempt &lt; MAX_LLM_RETRIES?}
  ATTEMPT -- no --> EXHAUST([return None])
  ATTEMPT -- yes --> DO[await ctx.completion<br/>messages, tools, role=ATTACKER]
  DO -->|ok response| OK([return response])
  DO -->|exception exc| CLASSIFY{_is_rate_limit_error exc?}

  CLASSIFY -- yes --> COOLKEY["_cool_down_key_for ctx, exc<br/>KeyPool.cool_down until = compute_cooldown exc"]
  COOLKEY --> ACTIVE{ctx.key_pool.active_count == 0?}
  ACTIVE -- yes --> WALL([log RATE_LIMIT_WALL<br/>return None])
  ACTIVE -- no --> ROT[next attempt — KeyPool rotates]
  ROT --> ATTEMPT

  CLASSIFY -- no --> TRANSIENT{transient / network?}
  TRANSIENT -- yes --> SLEEP[asyncio.sleep<br/>RETRY_DELAYS attempt]
  SLEEP --> ATTEMPT
  TRANSIENT -- no --> HARD_ERR([log LLM_ERROR<br/>return None])
```

---

## State touched per iteration

```mermaid
flowchart LR
  LOOP([one iteration]) --> CTX[Context]
  LOOP --> MSGS[messages list<br/>OpenAI format]
  LOOP --> GRAPH[AttackGraph]
  LOOP --> MEM[TargetMemory]
  LOOP --> TEL[RunTelemetry]
  LOOP --> TGT[Target adapter]

  CTX -- ctx.turns --> MSGS
  CTX -- ctx.telemetry --> TEL
  CTX -- ctx.send → target.send --> TGT
  CTX -- ctx.run_module → nested loop --> LOOP

  MSGS -.compressed in place.-> MSGS
  GRAPH -.updated on sub-module judge.-> GRAPH
  MEM -.save_graph / save_conversation after run.-> GRAPH

  classDef hot fill:#922b21,color:#fff,stroke:#641e16
  class MSGS,GRAPH hot
```

---

## Source map

| Concern | File |
|---|---|
| Outer loop, prompt assembly, circuit breaker, `conclude` short-circuit | `mesmer/core/agent/engine.py::run_react_loop` |
| Retry + key rotation | `mesmer/core/agent/retry.py::_completion_with_retry` |
| Tool list + dispatch table | `mesmer/core/agent/tools/__init__.py` |
| Per-tool schema + handler | `tools/send_message.py`, `tools/ask_human.py`, `tools/conclude.py`, `tools/sub_module.py` |
| Graph-context / budget / frontier prompt blocks | `mesmer/core/agent/prompt.py` |
| Judge call, graph update, reflection | `mesmer/core/agent/evaluation.py` |
| In-loop judge LLM | `mesmer/core/agent/judge.py` |
| CONTINUOUS-mode compression | `mesmer/core/agent/compressor.py` |
| Prose prompts | `mesmer/core/agent/prompts/*.prompt.md` |
