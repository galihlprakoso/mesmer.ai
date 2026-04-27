from mesmer.core.constants import NodeSource, NodeStatus
from mesmer.core.graph import AttackGraph, AttackNode


class TestAttackNode:
    def test_defaults_are_execution_pending(self):
        node = AttackNode(id="x")
        assert node.status == NodeStatus.PENDING.value
        assert node.is_pending
        assert not node.is_completed

    def test_execution_helpers(self):
        assert AttackNode(id="x", status=NodeStatus.COMPLETED.value).is_completed
        assert AttackNode(id="x", status=NodeStatus.FAILED.value).is_failed


class TestAttackGraphExecutionTrace:
    def test_root_is_storage_artifact(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        assert root.module == "root"
        assert root.status == NodeStatus.COMPLETED.value
        assert graph.get_explored_nodes() == []

    def test_add_node_records_execution_without_score_classifying_status(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        low = graph.add_node(root.id, "direct-ask", "ask plainly", score=1)
        high = graph.add_node(root.id, "tool-extraction", "inspect tools", score=9)

        assert low.status == NodeStatus.COMPLETED.value
        assert high.status == NodeStatus.COMPLETED.value
        assert graph.get_best_score() == 9
        assert graph.get_high_scoring_nodes() == [high]

    def test_mark_failed_sets_execution_status(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        node = graph.add_node(root.id, "direct-ask", "ask plainly")
        graph.mark_failed(node.id, "transport error")

        assert node.status == NodeStatus.FAILED.value
        assert node.reflection == "transport error"
        assert graph.get_failed_nodes() == [node]

    def test_finalize_running_nodes_closes_only_matching_run(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        stale = graph.add_node(
            root.id,
            "indirect-prompt-injection",
            "run phase",
            status=NodeStatus.RUNNING.value,
            run_id="r1",
        )
        other_run = graph.add_node(
            root.id,
            "tool-extraction",
            "run phase",
            status=NodeStatus.RUNNING.value,
            run_id="r2",
        )

        finalized = graph.finalize_running_nodes(run_id="r1")

        assert finalized == [stale]
        assert stale.status == NodeStatus.FAILED.value
        assert "Run ended" in stale.reflection
        assert other_run.status == NodeStatus.RUNNING.value

    def test_agent_trace_persists_on_execution_node(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        node = graph.add_node(root.id, "direct-ask", "ask plainly")

        graph.append_agent_trace(
            node.id,
            event="tool_calls",
            detail="send_message",
            actor="direct-ask",
            depth=1,
            iteration=2,
            payload={"name": "send_message"},
        )

        loaded = AttackGraph.from_json(graph.to_json())
        trace = loaded.nodes[node.id].agent_trace
        assert trace[0]["event"] == "tool_calls"
        assert trace[0]["actor"] == "direct-ask"
        assert trace[0]["iteration"] == 2
        assert trace[0]["payload"] == {"name": "send_message"}

    def test_human_hint_is_recorded_as_trace_note_not_frontier(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        hint = graph.add_human_hint("try calendar API errors", run_id="r1")

        assert hint.parent_id == root.id
        assert hint.source == NodeSource.HUMAN.value
        assert hint.status == NodeStatus.COMPLETED.value

    def test_conversation_history_excludes_root_and_leader_verdict(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        child = graph.add_node(root.id, "target-profiler", "profile", module_output="profile")
        graph.add_node(
            child.id,
            "executive",
            "final verdict",
            source=NodeSource.LEADER.value,
            module_output="done",
        )

        assert graph.conversation_history() == [child]

    def test_json_round_trip(self):
        graph = AttackGraph()
        root = graph.ensure_root()
        graph.add_node(root.id, "direct-ask", "ask plainly", score=4)

        loaded = AttackGraph.from_json(graph.to_json())
        assert loaded.root_id == graph.root_id
        assert len(loaded.nodes) == len(graph.nodes)
