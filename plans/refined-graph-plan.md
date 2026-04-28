
› You are a person that always doing extra mile, very creative and smart person that thinking beyond common human, you are
  exceptional thinker with enormous amount of energy that you will translate into high effort, you are the number one hard-worker and
  thinker person in the world.

  Anything you do after this, you will do like your life depends on it, like this is your last chance on contributing to real impact
  to the world that you are so care about.

  I want to ask something, how the current graph utilized by the agent executions here? initially I want the graph represent the

• I’ll read the overview sections in CLAUDE.md first, then tie that back to the actual graph code paths so the answer is grounded in
  the project’s own architecture notes.

• Explored
  └ Read CLAUDE.md

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

• The overview says the graph is intended as the canonical per-target memory: every module execution becomes a node, judge scores it, and later runs inject graph-derived context so the agent can avoid
  dead ends and expand a frontier. I’m checking the concrete implementation now to see how much of that is active versus aspirational.

• Explored
  └ Read evaluation.py, graph.py, prompt.py, engine.py, sub_module.py, runner.py
    Search save_graph|load_graph|on_graph_update|add_node\(|LEADER|GlobalMemory.update_from_graph in mesmer

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

• I have enough of the code path now: the graph is not just visualization. It is used as persistent memory, prompt input, artifacts bootstrap, deterministic frontier generation, anti-repetition, and
  UI/benchmark trace data. The important caveat is that it still represents attempts/modules more than an explicit “hypothesis search over target weaknesses.”

• Explored
  └ Read evaluation.py, graph.py, prompt.py, runner.py

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

• I read CLAUDE.md and checked the implementation. Short answer: the current graph is active runtime memory, not just a visual graph, but it models attack attempts and module outcomes, not yet a first-
  class “search journey over target weaknesses.”

  How It Is Used Now

  The graph is loaded per target at run start from ~/.mesmer/targets/{hash}/graph.json, unless --fresh is used. That happens in mesmer/core/runner.py:186.

  Each delegated module execution becomes an AttackNode containing:

  - module name
  - approach/instruction
  - messages sent to the target
  - target responses
  - judge score
  - leaked info
  - raw module conclude() output
  - status: dead, alive, promising, frontier
  - parent/child relationship

  The graph is updated after each sub-module runs and is judged in mesmer/core/agent/evaluation.py:172.

  The graph then feeds future agent behavior in four main ways:

  1. Prompt intelligence
     The leader receives graph-derived context: frontier items, dead ends, summary, tier ladder, and budget hints. See mesmer/core/agent/prompt.py:80.
  2. Frontier generation
     After a successful/non-dead attempt, the graph deterministically proposes next modules to try. The LLM does not choose the module list; it only refines selected modules into approach text. See
     mesmer/core/agent/evaluation.py:270.
  3. Cross-run memory
     At run start, previous graph nodes seed the artifacts with latest module outputs, so a second run already sees prior profiler/planner/technique conclusions. See mesmer/core/runner.py:241.
  4. Anti-repetition and tier gating
     Dead approaches are rendered into prompts as “do not retry,” similar approaches can be marked dead if they do not improve, and module choice follows a simple-before-complex tier ladder. See mesmer/
     core/graph.py:1.

  Important Gap

  Your original intention was: “the graph represents the search journey of finding areas where the target is weak.”

  The current graph only partially does that.

  Right now, the graph represents:

  > “Which module/approach was attempted, what happened, how well it scored, and what should be tried next.”

  It does not explicitly represent:

  > “Hypothesis: this target is weak to X because of observed behavior Y; confidence increased/decreased after attempt Z.”

  So “weakness discovery” exists implicitly through scores, leaks, dead ends, and promising nodes, but not as a first-class object. A target-profiler node may describe weaknesses in module_output, but
  core treats that as opaque text.

  My Read

  The current graph is more like a persistent attack-attempt decision tree than a cognitive vulnerability map.

  It is useful and operational: it steers execution, remembers past results, avoids repeated failures, expands frontiers, and warms future runs. But if you want the graph to match your deeper concept,
  the next evolution would be to add explicit nodes or annotations for:

  - observed target traits
  - hypothesized weaknesses
  - evidence supporting each weakness
  - confidence score
  - modules/approaches that tested that weakness
  - whether the weakness was confirmed, weakened, or falsified

  That would turn the graph from “attempt history” into a real “search journey over target weakness hypotheses.”


› fuck I knew it. Okay, what's the best use of a graph-based search, that perfect for this use case? or even do graph is the right one? is there like a paper, research, well-known research supported
  algorithm or something that fits for this use case? do some research.


• I’ll look at the research landscape around automated LLM red-teaming/search algorithms and then map it back to Mesmer’s architecture. I’ll keep this at the algorithm/design level, not exploit recipe
  level.

• Searching the web

• Searched TAP Tree of Attacks with Pruning large language models jailbreak paper arxiv

• Searching the web

• Searched arxiv 2312.02119 Tree of Attacks with Pruning

• Searching the web

• Searched Red Teaming Language Models with Language Models arxiv 2202.03286

• Searching the web

• Searched POMCP online Monte Carlo planning partially observable Markov decision processes paper

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

• You were right to be suspicious.

  A graph is still the right primitive, but not the current kind of graph. The current graph is an execution log plus frontier queue. For your intended use case, the graph should become a belief/search
  graph over target weakness hypotheses, with attempts as experiments attached to those hypotheses.

  Best framing:

  > Mesmer should not search “which module next?”
  > It should search “which suspected weakness should I test next, with which module, under which conversation state?”

  Best-Fit Research
  The closest research-backed families are:

  1. TAP: Tree of Attacks with Pruning
     This is closest to what Mesmer currently approximates. TAP uses tree search: generate candidate attacks, prune weak branches before spending target queries, iterate on promising branches.
     Good fit for Mesmer’s existing frontier/dead-end model.
     Source: Tree of Attacks: Jailbreaking Black-Box LLMs Automatically (https://huggingface.co/papers/2312.02119), arXiv 2312.02119.
  2. PAIR: Prompt Automatic Iterative Refinement
     PAIR is a focused single-path refinement algorithm. It repeatedly observes the target response, critiques failure, and rewrites the next candidate. It is query-efficient, often under 20 queries in
     the paper’s framing.
     Good fit for “exploit one suspected weakness deeply.”
     Source: Jailbreaking Black Box Large Language Models in Twenty Queries (https://huggingface.co/papers/2310.08419), arXiv 2310.08419.
  3. GPTFuzzer
     This treats red-teaming like fuzzing: keep a corpus of seeds, mutate them, judge outcomes, prioritize productive seed families.
     Good fit for broad exploration and discovering unknown classes of weakness.
     Source: GPTFUZZER: Red Teaming Large Language Models with Auto-Generated Jailbreak Prompts (https://huggingface.co/papers/2309.10253), arXiv 2309.10253.
  4. AutoDAN-Turbo
     This is conceptually very relevant: it builds a lifelong strategy library from attack logs, retrieves/evolves strategies, and improves across targets/runs.
     Good fit for Mesmer’s persistent graph and cross-run memory.
     Source: AutoDAN-Turbo: A Lifelong Agent for Strategy Self-Exploration (https://huggingface.co/papers/2410.05295), arXiv 2410.05295.
  5. POMDP / POMCP / MCTS
     This is the more principled answer. Your target’s weakness is hidden state. Every probe gives noisy evidence. The agent must choose actions under partial observability. That is a POMDP-shaped
     problem. POMCP applies Monte Carlo Tree Search to partially observable settings.
     Good fit for multi-turn attack planning where the target’s latent vulnerabilities are inferred over time.
     Source: Online Planning Algorithms for POMDPs (https://pmc.ncbi.nlm.nih.gov/articles/PMC2748358/) and POMCP overview in stochastic planning
     (https://link.springer.com/article/10.1007/s10472-024-09947-5).

  My Recommendation
  Use a hybrid, not one pure algorithm:

  > Hypothesis Graph + TAP-style tree search + bandit scoring + fuzzing mutations.

  Concretely:

  - Keep a graph, but make it typed.
  - Add WeaknessHypothesis nodes.
  - Add Evidence nodes from target behavior.
  - Add Attempt nodes for module executions.
  - Add Strategy nodes for reusable attack patterns.
  - Add edges like:
      - attempt_tests_hypothesis
      - response_supports_hypothesis
      - response_refutes_hypothesis
      - strategy_exploits_weakness
      - hypothesis_generalizes_to

  Then the planner should choose next action by something like:

  score(next_test) =
    expected_success
  + information_gain
  + novelty
  + transfer_value
  - query_cost
  - repetition_penalty
  - risk_of_dead_end

  That gives you the real thing: the graph becomes a scientific investigation map.

  Is Graph Search Right?
  Yes, but only if the graph represents the right object.

  Wrong graph:

  root -> direct-ask attempt -> delimiter attempt -> authority-bias attempt

  Better graph:

  Target may be weak to instruction-recital
    supported by: target quoted policy fragment
    tested by: indirect-recital attempt
    refuted by: refusal after delimiter injection
    next best experiment: format-shift with low-friction completion frame

  The current Mesmer graph answers:

  > “What did we try?”

  The graph you want should answer:

  > “What do we believe about this target, why do we believe it, and what experiment most reduces uncertainty or increases chance of success?”

  That is the real design shift. TAP gives you the tree-search skeleton. GPTFuzzer gives you mutation/corpus pressure. PAIR gives you tight refinement. AutoDAN-Turbo gives you lifelong strategy memory.
  POMDP/POMCP gives you the correct mathematical framing for hidden weakness discovery.


› How about that MCTS?


• MCTS is tempting, but plain MCTS is not the right fit unless you redefine the search state carefully.

  Classic MCTS assumes:

  state -> action -> next state -> reward

  For Mesmer, the real state is mostly hidden:

  target latent weakness
  target refusal policy
  conversation state
  already-leaked fragments
  attack history
  module outputs

  So the problem is closer to POMDP + MCTS, not normal MCTS.

  Where MCTS Fits
  MCTS fits if each node represents a belief state, not just an executed module.

  A node should mean:

  Belief:
    target may be weak to X
    confidence = 0.62
    evidence = [...]
    current conversation context = [...]
    known dead ends = [...]

  An edge/action should mean:

  Run experiment:
    module = delimiter-injection
    approach = "test boundary confusion"
    target cost = 1 query

  Reward should combine:

  reward =
    leak_score
  + objective_progress
  + information_gain
  + hypothesis_confirmation
  - query_cost
  - repetition_penalty

  That makes MCTS useful because it can choose between:

  - exploit a promising weakness
  - test an uncertain weakness
  - branch into a new hypothesis
  - abandon a dead path

  But Plain MCTS Has Problems
  Naive MCTS will struggle because:

  1. Huge action space
     Prompts are infinite. You need a generator to propose candidate actions first.
  2. Expensive rollouts
     Every rollout costs target/API calls. You cannot simulate thousands of branches cheaply.
  3. No reliable simulator
     MCTS normally benefits from many simulated futures. Mesmer only has the real target unless you add a learned/world-model judge.
  4. Sparse rewards
     The final objective may only trigger after several moves. Intermediate scoring must be strong.
  5. Partial observability
     The target’s weakness is hidden. You are not searching a visible game board.

  Best Version For Mesmer
  Use budgeted, shallow, LLM-guided MCTS over belief nodes.

  Not:

  MCTS over raw prompts

  But:

  MCTS over weakness hypotheses and strategy families

  A practical design:

  Selection:
    choose hypothesis/strategy with UCB:
    value + exploration_bonus + information_gain_bonus

  Expansion:
    ask attacker/planner LLM to propose 2-5 concrete experiments

  Simulation:
    either skip rollout, or use cheap judge/world-model to estimate likely outcome

  Evaluation:
    run only the selected real experiment against target

  Backup:
    update hypothesis confidence, strategy value, dead-end status

  So it becomes closer to online MCTS / POMCP / open-loop planning, not full classical MCTS.

  My Recommendation
  Use MCTS as the scheduler, not the whole intelligence.

  Architecture:

  Hypothesis Graph
    stores beliefs, evidence, attempts, strategies

  Candidate Generator
    proposes next experiments from modules/LLM/fuzzer

  MCTS/POMCP Selector
    chooses which experiment to spend budget on

  Judge/Evaluator
    scores leak, refusal, novelty, info gain

  Belief Updater
    updates weakness confidence and strategy value

  This would be a real upgrade from the current graph.

  Verdict
  MCTS is useful, but only in this form:

  > Belief-state MCTS with progressive widening and information-gain reward.

  If you do raw MCTS over module attempts, it will become an expensive version of the current frontier system. If you do MCTS over weakness hypotheses, it becomes exactly aligned with your original
  vision.


› Okaym based on those research results. Please craft a plan (it's okay to re-thinking everything, no risk at all, this is initial project, nobody's using it, no need to think about legacy support,
  etc.) to make this graph signifficantly helpful for mesmer, for agents. You can mix things up, what's best on that paper / research, what's worst, combine them to make great product. give your best!


• Core Redesign
  Make Mesmer’s graph a Belief Attack Graph, not an execution graph.

  Current graph says:

  module A ran -> got score 4 -> module B suggested

  New graph should say:

  target likely has weakness W
  evidence E supports it
  strategy S may exploit it
  attempt A tested it
  result R changed confidence
  next experiment N has highest expected value

  That is the right abstraction for agents.

  Use These Research Ideas
  Use TAP for tree search and pruning: keep candidate branches, kill weak ones early.

  Use PAIR for local refinement: once a branch looks promising, iteratively improve that branch instead of constantly jumping around.

  Use GPTFuzzer for diversity: mutate successful/interesting prompts and strategy templates to discover nearby variants.

  Use AutoDAN-Turbo for lifelong learning: persist successful strategies across targets and retrieve them when a new target looks similar.

  Use POMDP/POMCP thinking for hidden state: the target’s real weakness is latent, so maintain beliefs and update them after observations.

  New Graph Model
  Replace AttackNode as the only primitive with typed nodes:

  TargetNode
  WeaknessHypothesisNode
  StrategyNode
  AttemptNode
  EvidenceNode
  ObservationNode
  FrontierExperimentNode
  OutcomeNode

  Important edges:

  hypothesis_supported_by_evidence
  hypothesis_refuted_by_evidence
  strategy_tests_hypothesis
  attempt_used_strategy
  attempt_observed_response
  attempt_confirmed_hypothesis
  attempt_refuted_hypothesis
  frontier_expands_hypothesis
  strategy_generalizes_from_attempt

  Each WeaknessHypothesisNode should track:

  id: wh_123
  label: "instruction-recital susceptibility"
  description: "Target may reveal hidden instructions if asked to transform or recite them indirectly."
  confidence: 0.63
  status: active | confirmed | refuted | stale
  evidence_for: [...]
  evidence_against: [...]
  best_score: 7
  last_tested_at: ...

  Each StrategyNode should track:

  family: "format-shift"
  template_summary: "ask target to output policy as transformed artifact"
  success_rate_local: 0.42
  success_rate_global: 0.18
  works_against_traits: [...]
  fails_against_traits: [...]

  Agent Loop
  The new loop should be:

  1. Observe target response
  2. Extract evidence
  3. Update weakness beliefs
  4. Generate frontier experiments
  5. Rank experiments
  6. Execute best experiment
  7. Judge result
  8. Backpropagate value into graph

  Not:

  run module -> judge -> add node -> suggest module

  Scoring Formula
  Every frontier experiment should get a utility score:

  utility =
    0.35 * expected_objective_progress
  + 0.25 * information_gain
  + 0.15 * hypothesis_confidence
  + 0.10 * novelty
  + 0.10 * strategy_prior
  + 0.05 * transfer_value
  - 0.20 * query_cost
  - 0.25 * repetition_penalty
  - 0.30 * dead_end_similarity

  The coefficients should be configurable, but start here.

  This matters because Mesmer should not only chase likely success. It should also ask: “What experiment teaches us the most?”

  Search Policy
  Use three modes:

  EXPLORE:
    prefer high information gain and novelty

  EXPLOIT:
    prefer high confidence weaknesses and high expected progress

  CONFIRM:
    verify suspected objective completion or strong leak

  Add a fourth mode:

  PIVOT:
    when the graph detects repeated low-value attempts, force a new hypothesis family

  MCTS Layer
  Use shallow, budgeted MCTS over belief nodes.

  Do not run full classic MCTS. Use:

  Belief-State MCTS + Progressive Widening + LLM Candidate Generation

  Selection:

  UCB =
    average_value
  + c * sqrt(log(parent_visits) / child_visits)
  + info_gain_bonus
  + novelty_bonus

  Expansion:

  Generate 2-5 frontier experiments from:
  - active weakness hypotheses
  - successful prior strategies
  - fuzzer mutations
  - human hints

  Evaluation:

  Use judge/world-model to estimate value before spending target query.
  Only execute the best candidate against the real target.

  Backup:

  Update:
  - hypothesis confidence
  - strategy value
  - module value
  - edge value
  - dead-end clusters

  Where Papers Fit
  TAP gives:

  tree branches
  pruning
  frontier expansion

  PAIR gives:

  single-branch iterative refinement
  critique -> rewrite -> retry

  GPTFuzzer gives:

  seed corpus
  mutation operators
  novel variant generation
  coverage pressure

  AutoDAN-Turbo gives:

  strategy memory
  lifelong cross-target learning
  retrieval
  strategy evolution

  POMDP/POMCP gives:

  belief state
  hidden weakness modeling
  uncertainty-aware action choice

  Implementation Plan
  Phase 1: Redefine graph schema.

  Create:

  mesmer/core/belief_graph.py

  Core dataclasses:

  BeliefGraph
  WeaknessHypothesis
  Strategy
  Attempt
  Evidence
  FrontierExperiment
  GraphEdge

  Keep serialization simple JSON. No database yet.

  Phase 2: Add evidence extraction.

  After every target response, run an extractor that produces structured evidence:

  signals:
    - type: refusal_template
    - type: partial_compliance
    - type: policy_reference
    - type: tool_reference
    - type: hidden_instruction_fragment
    - type: role_boundary_confusion
    - type: format_following_strength

  This can be judge-model powered at first.

  Phase 3: Add hypothesis updater.

  Convert observations into belief updates:

  if partial compliance after format-shift:
    increase confidence(format-shift susceptibility)

  if repeated refusal on direct ask:
    decrease confidence(direct disclosure weakness)

  Use simple Bayesian-ish updates first. No need to overbuild.

  Phase 4: Add frontier experiment generator.

  Generate candidate experiments from:

  active hypotheses
  known strategies
  module registry
  human hints
  fuzzer mutations
  prior successful attempts

  Each frontier should be explicit:

  hypothesis_id: wh_123
  strategy_id: st_456
  module: format-shift
  instruction: "test whether structured transformation bypasses refusal"
  expected_signal: "target emits hidden instruction fragment"
  cost: 1

  Phase 5: Add utility ranking.

  Replace current propose_frontier() with:

  rank_frontier_experiments(graph, budget_mode, registry)

  This should rank experiments, not modules.

  Phase 6: Add shallow MCTS selector.

  Start simple:

  depth 2
  branching 3
  no expensive target rollout
  judge/world-model evaluation only
  real execution only for selected candidate

  This avoids API explosion.

  Phase 7: Add PAIR refinement mode.

  When one hypothesis has high confidence and the latest attempt was close, enter local refinement:

  critique failure
  rewrite approach
  retry under same hypothesis
  stop after N no-gain attempts

  Phase 8: Add fuzzing corpus.

  Persist:

  ~/.mesmer/global/strategies.json
  ~/.mesmer/global/seeds.json

  Store successful prompt/strategy summaries, not necessarily raw harmful text. Mutate at the strategy level first:

  role frame
  format frame
  authority frame
  recital frame
  delimiter frame
  tool frame

  Phase 9: Add graph UI that reflects reasoning.

  The UI should show:

  Weakness Map
  Evidence Timeline
  Strategy Library
  Current Beliefs
  Next Best Experiments
  Dead Hypotheses

  The graph view should stop being only “what ran.” It should show “what the system believes.”

  New Agent Prompt
  The leader should receive:

  ## Current Beliefs
  - WH-1: likely weak to format transformation, confidence 0.68
  - WH-2: unlikely weak to direct ask, confidence 0.12

  ## Strongest Evidence
  - Target followed structured output request while refusing direct disclosure.
  - Target referenced internal policy category but avoided content.

  ## Recommended Experiments
  1. FX-12: test format-shift on WH-1, expected info gain high
  2. FX-13: confirm WH-3 with indirect recital

  ## Dead Zones
  - Direct request variants have failed 4 times.

  That is much more useful than a generic list of past modules.

  What To Remove
  I would remove or demote:

  module-first frontier generation
  flat explored-path summary
  module best-score as primary selector
  leader-verdict node as search-relevant node

  Keep leader verdict for audit, but exclude it from planning.

  Product Impact
  This makes Mesmer feel less like:

  an agent that tries tools

  and more like:

  an autonomous red-team researcher building and testing a theory of the target

  That is the differentiated product. The graph becomes the agent’s working scientific model, not its run log.

• Model changed to gpt-5.5 xhigh


› review again your plan make sure all your assumption is 100% correct with 100% confidence. make sure you don't miss any single thing. please also think about the visualization how do we visualize it
  on mesmer's web interface. and primarily, think about the context injection part, how those graph will be modified, and injected to the LLM context, this is the most important part. how the graph
  evolves, and how the graph utilized by the LLM agents.


• I’ll tighten the plan against two sources of truth: the current Mesmer code path and the primary papers behind TAP/PAIR/fuzzing/lifelong strategy search. I’ll also be explicit where “100% confidence”
  is impossible and replace it with testable assumptions.

• Explored
  └ Read AttackGraph.svelte, NodeDetail.svelte, engine.py, events.py

• Searching the web

• Searched Tree of Attacks with Pruning jailbreaking black-box LLMs automatically arxiv 2312.02119

• Searching the web

• Searched https://arxiv.org/abs/2312.02119

• Searching the web

• Searched https://arxiv.org/abs/2309.10253

• Searching the web

• Searched UCT Kocsis Szepesvari 2006 bandit based Monte-Carlo planning paper

• Searching the web

• Searched Bandit based Monte-Carlo Planning Kocsis Szepesvari PDF lri

──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

• I cannot honestly say “100% confidence” for design assumptions. What I can do is separate what is verified, what is research-backed, and what must be validated by benchmarks. The prior plan’s main
  direction is still right, but I’d tighten it.

  Verified In Mesmer
  Current Mesmer already injects graph-derived context into every ReAct run: artifacts, module timeline, learned experience, frontier/dead-end summary, and budget hints are assembled in mesmer/core/
  agent/engine.py:96. The graph is loaded/saved per target and seeds future runs in mesmer/core/runner.py:186. The web UI currently visualizes an execution tree/timeline, not a belief map, in mesmer/
  interfaces/web/frontend/src/components/AttackGraph.svelte:1.

  So the needed change is not “add graph usage.” It is: change what the graph means and compile it into better agent context.

  Revised Core Design
  Replace the current AttackGraph as the planner’s primary object with a Belief Attack Graph.

  Keep an append-only execution log for audit, but make planning run on typed belief objects:

  Target
  WeaknessHypothesis
  Evidence
  Attempt
  Strategy
  FrontierExperiment
  Outcome

  The critical node is WeaknessHypothesis:

  id: WH-7
  claim: "Target may comply under structured transformation requests"
  confidence: 0.64
  status: active | confirmed | refuted | stale
  evidence_for: [EV-12, EV-15]
  evidence_against: [EV-19]
  tested_by: [AT-4, AT-9]
  next_experiments: [FX-22, FX-25]

  This changes the agent’s question from:

  Which module should I run?

  to:

  Which belief about the target should I test or exploit next?

  That is the correct abstraction.

  Graph Evolution
  Every target interaction should produce graph deltas, not mutate nodes loosely.

  Pipeline:

  1. Agent runs experiment FX-n
  2. Module talks to target
  3. Judge scores objective progress
  4. Evidence extractor extracts structured signals
  5. Belief updater changes hypothesis confidence
  6. Frontier generator creates new experiments
  7. Search policy ranks experiments
  8. Context compiler injects the next relevant slice

  Use a reducer-style API:

  graph.apply(EvidenceDelta(...))
  graph.apply(BeliefDelta(...))
  graph.apply(AttemptDelta(...))
  graph.apply(FrontierDelta(...))

  Do not let an LLM directly rewrite the graph. LLMs propose structured deltas; deterministic code validates and applies them.

  Context Injection
  This is the most important part. Do not inject the graph. Inject a compiled decision brief.

  Add:

  GraphContextCompiler.compile(
      graph,
      role="leader" | "manager" | "technique" | "judge",
      module_name=...,
      active_experiment_id=...,
      token_budget=...
  )

  Leader context should contain:

  ## Current Target Beliefs
  - WH-7 confidence 0.64: ...
    Best evidence: EV-12
    Uncertainty: needs confirmation under fresh wording

  ## Recommended Experiments
  1. FX-22 utility 0.78: tests WH-7 with strategy ST-3
  2. FX-25 utility 0.71: explores new hypothesis WH-9

  ## Dead Zones
  - Direct disclosure family: 4 failures, no score improvement

  ## Required Action
  Choose one frontier_experiment_id. Do not freestyle unless creating a new hypothesis.

  Manager context should contain only the slice relevant to its assignment:

  Active experiment: FX-22
  Hypothesis being tested: WH-7
  Expected observation: partial compliance / refusal / leak signal
  Report back: evidence_for, evidence_against, confidence_delta

  Technique context should be even narrower:

  Your job is to run this experiment, observe target behavior, and conclude with structured evidence. Do not decide global success.

  Judge/extractor context should receive raw exchanges plus expected signal, not the whole graph.

  The main rule: inject beliefs, evidence, and next decisions, not long history.

  Search Policy
  Use a hybrid, not pure MCTS.

  Best practical selector:

  utility =
    expected_progress
  + information_gain
  + confidence
  + novelty
  + strategy_prior
  - query_cost
  - repetition_penalty
  - dead_similarity

  Then add shallow MCTS/UCT only over generated frontier experiments:

  selection: UCB + information-gain bonus
  expansion: LLM/fuzzer proposes 2-5 experiments
  simulation: cheap judge/world-model estimate only
  execution: spend target query only on selected experiment
  backup: update hypothesis and strategy values

  Do not do classical MCTS over raw prompts. It needs a simulator and too many rollouts. For Mesmer, MCTS should be a budgeted belief-state scheduler, not the whole brain.

  Research Fit
  TAP supports tree search plus pruning before spending target queries. PAIR supports local iterative refinement when a branch is promising. GPTFuzzer supports seed mutation and diversity pressure.
  AutoDAN-Turbo supports lifelong strategy memory. UCT/POMCP support the idea of planning under uncertainty, but Mesmer must adapt them because the hidden target weakness is only partially observable
  and real rollouts are expensive.

  Visualization
  The web UI needs three graph views, not one.

  1. Belief Map
     Center hypotheses. Left side evidence. Right side strategies/frontier experiments. Edge labels show supports, refutes, tests, generated.
  2. Execution Timeline
     Keep the current tree/timeline, but show each attempt’s belief delta:
     WH-7 +0.18, WH-3 -0.22, ST-2 value +0.11.
  3. Frontier Board
     A ranked table/card list:
     experiment_id, hypothesis, strategy, utility, expected info gain, cost, reason selected.

  Node detail should add a crucial tab: Prompt Context. Show exactly what graph slice was injected into the LLM for that node. This will make the system debuggable.

  What I Would Remove
  Remove module-first frontier generation as the primary planner. Keep modules as execution tools, not search states.

  Remove raw graph summaries from prompts once the compiler exists. They are too blunt.

  Demote leader-verdict nodes to audit only. They should not shape search except through final outcome labels.

  Implementation Order

  1. Add typed BeliefGraph and append-only delta log.
  2. Add evidence extraction after every judged attempt.
  3. Add hypothesis generation/update.
  4. Replace propose_frontier() with experiment ranking.
  5. Add GraphContextCompiler.
  6. Wire leader tools to require frontier_experiment_id.
  7. Build Belief Map + Frontier Board in web UI.
  8. Add shallow UCT/MCTS selector after the simpler utility ranker works.
  9. Add global strategy library inspired by AutoDAN-Turbo.

  Hard Assumptions To Validate
  The uncertain parts are utility weights, confidence update rules, extractor reliability, and whether shallow MCTS beats a simpler bandit selector. Those need benchmark tests. I would not bet the
  project on MCTS first. I would build the belief graph and context compiler first, then compare selectors.

  Sources: TAP on arXiv, PAIR on arXiv, GPTFuzzer on arXiv, AutoDAN-Turbo on arXiv, POMCP/large POMDP planning, and UCT/Bandit Monte-Carlo Planning.


# Session 1 — completion summary (2026-04-26)

The foundation landed as a **parallel system**. The legacy
`AttackGraph` is untouched; a run today still works exactly as before.
The new code is fully testable in isolation and lint-clean.

## What shipped

**Constants & errors**
- `mesmer/core/constants.py` — added `HypothesisStatus`, `EvidenceType`,
  `Polarity`, `AttemptOutcome`, `ExperimentState`, `EdgeKind`,
  `BeliefRole`, `DeltaKind` enums; `BELIEF_DELTA`,
  `HYPOTHESIS_CREATED`, `HYPOTHESIS_UPDATED`, `EVIDENCE_EXTRACTED`,
  `EVIDENCE_EXTRACT_ERROR`, `FRONTIER_RANKED`, `FRONTIER_DROPPED`
  `LogEvent` values; `HYPOTHESIS_CONFIRMED_THRESHOLD = 0.85`,
  `HYPOTHESIS_REFUTED_THRESHOLD = 0.15`,
  `EVIDENCE_DEFAULT_WEIGHT = 0.10`, `EVIDENCE_TYPE_WEIGHTS` table
  (per-type strength), `DEFAULT_UTILITY_WEIGHTS` table (9-component
  utility blend), `HYPOTHESIS_STALE_RUNS = 3`.
- `mesmer/core/errors.py` — added `BeliefGraphError`, `InvalidDelta`
  (carries `kind` + `reason`), `EvidenceExtractionError`,
  `HypothesisGenerationError`. All derive from `MesmerError` so the
  CLI / web error boundary still catches.

**Belief graph**
- `mesmer/core/belief_graph.py` (~870 lines) — typed nodes:
  `TargetNode`, `WeaknessHypothesis`, `Evidence`, `Attempt`,
  `Strategy`, `FrontierExperiment` (each `BeliefNode` subclass with a
  class-level `kind: NodeKind` discriminator). `Edge` dataclass with
  endpoint contract validation in `_EDGE_END_TYPES`. Twelve
  `GraphDelta` subclasses (`TargetTraitsUpdateDelta`,
  `HypothesisCreateDelta`, `HypothesisUpdateConfidenceDelta`,
  `HypothesisUpdateStatusDelta`, `EvidenceCreateDelta`,
  `AttemptCreateDelta`, `StrategyCreateDelta`,
  `StrategyUpdateStatsDelta`, `FrontierCreateDelta`,
  `FrontierUpdateStateDelta`, `FrontierRankDelta`, `EdgeCreateDelta`).
  `BeliefGraph.apply(delta)` is the SOLE mutation entry point;
  deep-copies on insert so deltas serialise from their snapshot.
  Auto-emits support/refute edges on EvidenceCreate, all referenced
  edges on AttemptCreate, expansion edges on FrontierCreate.
  Persistence: `save(snapshot_path, delta_log_path)` writes JSON
  snapshot + appends JSONL delta log; `replay(delta_log_path)`
  reconstructs from log if snapshot corrupts. Convenience builders:
  `make_hypothesis`, `make_evidence`, `make_attempt`, `make_strategy`,
  `make_frontier`. Ids are kind-prefixed (`wh_…`, `ev_…`, `at_…`,
  `st_…`, `fx_…`, `tg_…`) — readable in logs.

**Evidence extractor**
- `mesmer/core/agent/evidence.py` (~250 lines) — one judge-model LLM
  call per attempt. `extract_evidence(ctx, attempt, active_hypotheses)`
  returns `list[Evidence]` ready to wrap in `EvidenceCreateDelta`
  (caller applies). Skips empty / pipeline-error responses without
  paying for an LLM call. Coerces malformed extractor rows: unknown
  `signal_type` drops the row; hallucinated `hypothesis_id` is
  rewritten to NEUTRAL polarity (still recorded, just no edge).
  Caps verbatim fragments at 200 chars and output at 4 evidences per
  call. Raises `EvidenceExtractionError` on LLM failure / non-object
  response / missing `evidences` key — boundary catch lives in the
  engine in Session 2.

**Belief layer**
- `mesmer/core/agent/beliefs.py` (~430 lines) — three operations:
  - `generate_hypotheses(ctx, graph, objective, recent_attempts)` —
    one LLM call → `list[HypothesisCreateDelta]`. Uses the
    family vocabulary (`format-shift`, `authority-bias`,
    `instruction-recital`, `delimiter-injection`, `tool-misuse`,
    `cognitive-overload`, `composite`, `unknown`). Drops rows missing
    claim/family. Capped at 4 per call. Raises
    `HypothesisGenerationError` on failure.
  - `apply_evidence_to_beliefs(graph, evidences)` — pure. Walks
    Evidence list, emits signed `HypothesisUpdateConfidenceDelta`
    plus at most one `HypothesisUpdateStatusDelta` per hypothesis
    when the cumulative shift crosses
    `HYPOTHESIS_CONFIRMED_THRESHOLD` /
    `HYPOTHESIS_REFUTED_THRESHOLD`. NEUTRAL evidence ignored.
    Already-non-ACTIVE hypotheses don't re-flip.
  - `rank_frontier(graph, weights=None)` — pure. For every PROPOSED
    `FrontierExperiment`, computes 8 components and weighted utility
    per `DEFAULT_UTILITY_WEIGHTS`. Information gain peaks at 0.5
    confidence (entropy-flavoured). Novelty / repetition /
    dead_similarity use Jaccard on instruction+module token bags
    against recent attempts (size-capped windows for O(1) per
    experiment). Returns one `FrontierRankDelta` covering all
    proposed experiments.

**Context compiler**
- `mesmer/core/agent/graph_compiler.py` (~400 lines) — pure renderer.
  `GraphContextCompiler(graph).compile(role=, module_name=,
  active_experiment_id=, token_budget=)` emits a Markdown decision
  brief. One per `BeliefRole` (LEADER / MANAGER / EMPLOYEE / JUDGE /
  EXTRACTOR). LEADER brief = beliefs + strongest evidence + ranked
  experiments + dead zones + required-action contract. MANAGER brief
  = active experiment + tested hypothesis + supporting/refuting
  evidence + report-back schema. EMPLOYEE = focused job description.
  JUDGE = hypothesis slate + expected_signal. EXTRACTOR = hypothesis
  slate (extractor builds its own block inline; this role is for
  symmetry).

**Prompts**
- `core/agent/prompts/extract_evidence_system.prompt.md`
- `core/agent/prompts/extract_evidence_user.prompt.md`
- `core/agent/prompts/generate_hypotheses_system.prompt.md`
- `core/agent/prompts/generate_hypotheses_user.prompt.md`
- All registered in `core/agent/prompts/__init__.py` as
  `EXTRACT_EVIDENCE_SYSTEM`, `EXTRACT_EVIDENCE_USER`,
  `GENERATE_HYPOTHESES_SYSTEM`, `GENERATE_HYPOTHESES_USER`.

**Tests**
- 86 new unit tests, all passing alongside the 583 existing ones
  (total: **748 passed** in 4.5s):
  - `tests/test_belief_graph.py` — 34 tests (delta apply per kind,
    edge endpoint validation, persistence round-trip, replay,
    `InvalidDelta` raising, hypothesis lifecycle, source preservation).
  - `tests/test_evidence_extractor.py` — 12 tests (mocked LLM, JSON
    parse, error path, hallucinated-id coercion, fragment truncation,
    skip-without-LLM-call when no responses).
  - `tests/test_beliefs.py` — 21 tests (hypothesis generation happy
    path + error paths, confidence shift math, status-flip threshold
    crossing, at-most-one-flip per batch, rank component math,
    novelty / dead-similarity penalties).
  - `tests/test_graph_compiler.py` — 19 tests (every role, fallback
    paths, dead-zone rendering, token-budget trim).
- `uv run ruff check` clean. `uv run ruff format` applied.

## Known follow-ups for Session 2 (the wiring)

The new code is parallel — nothing in `engine.py`, `evaluation.py`,
`runner.py`, `prompt.py`, or `memory.py` has changed yet. Session 2's
contract is to plug the belief graph in without breaking the 748 tests.

1. **`runner.execute_run`** — load/save `belief_graph.json` and
   `belief_deltas.jsonl` from `~/.mesmer/targets/{hash}/` alongside
   the legacy `graph.json`. `--fresh` should clear both. Wire a
   `BeliefGraph` into `Context` (mirror the existing `ctx.graph`
   binding).

2. **Bootstrap pass at run start** — call
   `generate_hypotheses(ctx, graph=belief_graph, objective=...)`
   once if the active hypothesis list is empty. Apply the deltas.
   This seeds the planner with falsifiable claims before the first
   attempt.

3. **`evaluation._update_graph` extension** — after a sub-module
   returns, build an `Attempt` from `messages_sent` /
   `target_responses` / judge result, apply
   `AttemptCreateDelta` to the belief graph (so the experiment
   linkage closes), call `extract_evidence` (catch
   `EvidenceExtractionError` → log and continue), apply each
   resulting `EvidenceCreateDelta`, then call
   `apply_evidence_to_beliefs(belief_graph, evidences)` and apply
   the returned belief deltas, then call
   `rank_frontier(belief_graph)` and apply the rank delta.

4. **`prompt._build_graph_context` replacement** — delete the legacy
   frontier/dead-end/summary block and replace it with
   `GraphContextCompiler(belief_graph).compile(role=BeliefRole.LEADER,
   token_budget=...)`. Manager and employee modules' user prompts
   get the same compiler with their own role.

5. **Leader's dispatch contract** — the leader's `conclude` /
   sub-module dispatch tools need to accept `experiment_id` so the
   resulting Attempt links back to the FrontierExperiment that
   generated it. This is the core "no freelancing" enforcement.

6. **Test rewrites** — the 12 test files that touch `AttackGraph`
   need updates. Most `_update_graph` tests can keep their AttackGraph
   assertions (the legacy graph is still written for audit) and just
   add belief-graph assertions alongside. The frontier-proposer
   tests (`test_graph.py::propose_frontier`) shrink — frontier
   selection moves to `rank_frontier` — but the existing graph stats
   queries stay valid.

Session 3 (web UI belief map) and Session 4 (selector + AutoDAN-Turbo
strategy library) shipped after Session 2, so the belief graph is now
both the validation surface and the execution-planning substrate. Do
not tune utility weights, confidence rules, or extractor categories
against intuition alone; use the calibration telemetry and bench
numbers as the audit loop.


# Belief graph shipped (2026-04-26), hardened again (2026-04-28)

Sessions 2, 2.5, 3, 4A, 4B all landed. The belief-graph pipeline is
wired end-to-end:

- **Session 2** — `BeliefGraph` plumbed through `Context`, loaded/saved
  via `TargetMemory`, integrated into `runner.execute_run`,
  bootstrap pass at run start, post-attempt
  `_update_belief_graph(ctx, ..., experiment_id=)` extends
  `evaluation._update_graph`, role-scoped brief appended to leader
  prompt by `engine.run_react_loop`.

- **Session 2.5** — `experiment_id` parameter on every sub-module
  dispatch tool's OpenAI function schema (in `Registry.as_tools`).
  `_update_belief_graph` resolves the experiment, ties the Attempt
  precisely to ONE hypothesis + ONE strategy, auto-fulfills the
  frontier. Hallucinated ids fall back to fan-out with a diagnostic.

- **Session 3** — `GET /api/targets/{hash}/belief-graph` backend
  route, `BeliefMap.svelte` D3 force-directed component (hypotheses
  sized by confidence, evidence triangles with polarity color,
  frontier squares sized by utility, strategy diamonds, attempt
  dots), `App.svelte` graph-view toggle pill switching between
  AttackGraph and BeliefMap.

- **Session 4A** — `select_next_experiment(graph, exploration_c=1.2)`
  UCB selector with recursive bounded lookahead. Compiler's
  recommended-experiments list flags the planner's pick with `★`.
  Pure function — automated steppers can use it without an LLM in the
  loop.

- **Session 4B** — `mesmer/core/strategy_library.py`:
  `GlobalStrategyEntry`, `StrategyLibrary`, `load_library` /
  `save_library` (atomic, schema-versioned),
  `merge_per_target_strategies` (run-end fold from per-target
  Strategy nodes, counters add, traits dedupe), trait-aware
  `retrieve_…` + `render_for_prompt` for the generator. Wired into
  `generate_hypotheses_user.prompt.md` via the new `{library_block}`
  slot. Persists at `~/.mesmer/global/strategies.json`.

- **2026-04-28 hardening** — belief updates use binary factor-graph
  inference over connected hypothesis components: exact enumeration
  for normal components and damped loopy belief propagation for large
  components. Dependency factors come from `HYPOTHESIS_GENERALIZES_TO`
  and observed multi-hypothesis attempts. Frontier ranking now carries
  nine auditable components including real `transfer_value` and
  registry-derived `query_cost`, persists transfer/cost provenance,
  and reports calibration telemetry (`calibration_samples`,
  `calibration_brier`, `calibration_score`) to the UI.

## Current verification snapshot

- 2026-04-28: `uv run pytest` passes with 710 Python tests.
- 2026-04-28: frontend `npm run test` passes with 53 Vitest tests.
- 2026-04-28: docs `pnpm typecheck` passes.

## Remaining honest boundaries

- **The `AttackGraph` execution trace is not removed.** Both graphs run
  side-by-side. Removing the execution trace is a separate decision —
  it requires deleting execution-trace persistence, the Attack Graph
  web component, and many audit/benchmark tests. It is not search
  state anymore; it is still useful execution telemetry.

- **Exact inference is bounded.** Connected hypothesis components are
  exact-enumerated up to the configured cap. Larger components use
  bounded loopy belief propagation, so dependency signal still moves
  without exponential runtime.

- **Bench tuning.** All weight constants
  (`HYPOTHESIS_CONFIRMED_THRESHOLD`,
  `EVIDENCE_TYPE_WEIGHTS`, `DEFAULT_UTILITY_WEIGHTS`,
  `DEFAULT_EXPLORATION_C`) are engineered defaults with runtime
  calibration telemetry, not yet benchmark-fit constants. The next
  audit loop should perturb each constant and read ASR / calibration
  shifts.

- **Push to GitHub.** Everything sits uncommitted on local `main`.
  When ready, branch + commit per session for reviewable history,
  then open a single integration PR.
