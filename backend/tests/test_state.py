"""
tests/test_state.py — General-purpose state + memory schema test suite.

HOW TO RUN (from project/backend/):
    python -m pytest tests/test_state.py -v

WHAT YOU WILL LEARN:
  This test suite proves that AgentState is WORKFLOW-AGNOSTIC.
  The same schema — the same graph infrastructure — powers every use case.
  You will see three different client workflows used as examples:
    - Supply chain risk analysis
    - Marketing campaign generation
    - Lead generation

  The tests also teach the five core LangGraph mechanics:
    1. Initial state defaults (what starts as None vs 0 vs [])
    2. Reducer behavior (how partial node returns merge into state)
    3. Partial update pattern (nodes return only what they changed)
    4. Memory injection (how context flows from Redis/Supabase to state)
    5. Guard rails (what must always be true regardless of workflow)

  Each test has a "WHAT THIS PROVES" comment explaining the architectural
  implication — not just the assertion.
"""

from uuid import uuid4

from app.state import (
    AgentState,
    MemoryContext,
    make_initial_state,
    append_errors,
    keep_latest,
)
from app.memory.schemas import (
    WorkflowRunRecord,
    WorkflowArtifactRecord,
    MemoryContextLoader,
)


# ---------------------------------------------------------------------------
# Fixtures — create clean starting states for each test
# ---------------------------------------------------------------------------

def fresh_state(query: str = "find 30 high-risk suppliers in East Java") -> AgentState:
    """Returns a clean initial state. The query doesn't matter for most tests."""
    return make_initial_state(user_query=query, run_id=str(uuid4()))


def supply_chain_context() -> MemoryContext:
    """Simulates context loaded from Redis/Supabase for a supply chain workflow."""
    return {
        "past_run_summaries": [
            "Last week: 8 high-risk suppliers flagged in Surabaya",
            "Last month: 23 suppliers downgraded due to audit failures",
        ],
        "benchmark_score": 0.74,
        "user_preferences": {"alert_threshold": "high", "report_format": "pdf"},
        "domain_context": {
            "approved_suppliers": ["PT Maju Jaya", "CV Sumber Rezeki"],
            "blocked_regions": ["Aceh", "Papua"],
            "risk_weights": {"audit_score": 0.4, "delivery_rate": 0.6},
        },
    }


# ---------------------------------------------------------------------------
# Group 1: Initial state structure
# Tests that make_initial_state() correctly seeds every field.
# ---------------------------------------------------------------------------

class TestInitialState:

    def test_user_query_is_set(self):
        """
        WHAT THIS PROVES: make_initial_state() correctly seeds user_query.
        This is the raw natural language input from the user or scheduler trigger.
        Every workflow starts here — the intent_node interprets this next.
        """
        state = make_initial_state(
            user_query="find 30 high-risk suppliers in East Java",
            run_id="run-001",
        )
        assert state["user_query"] == "find 30 high-risk suppliers in East Java"

    def test_workflow_type_initializes_none(self):
        """
        WHAT THIS PROVES: workflow_type starts as None and is set by intent_node.
        Before intent_node runs, NO agent should branch on workflow_type.
        This field is the routing key for department selection.

        Valid values after intent_node (open-ended — not an enum):
          "lead_gen" | "supply_chain" | "marketing" | "agriculture" | anything
        """
        state = fresh_state()
        assert state["workflow_type"] is None

    def test_department_initializes_none(self):
        """
        WHAT THIS PROVES: department starts as None and is set by planner_node.
        planner_node reads workflow_type and maps it to the responsible department.

        Example mapping (planner_node decides this, not hard-coded):
          "supply_chain"  → department = "analytics"
          "lead_gen"      → department = "distribution"
          "marketing"     → department = "content"
          "agriculture"   → department = "operations"  (new dept, just add a folder)

        In Phase 5, conditional edges read state["department"] to route to the
        correct department graph. Adding a new department = new folder + new graph.
        """
        state = fresh_state()
        assert state["department"] is None

    def test_artifacts_workflow_data_starts_none(self):
        """
        WHAT THIS PROVES: workflow_data starts as None — the primary artifact
        store is empty until nodes write into it. Nodes should check for None
        before reading, to detect "has the upstream node run yet?".

        This single field handles ALL workflow types:
          supply_chain nodes write:  {"suppliers": [...], "risk_map": {...}}
          lead_gen nodes write:      {"leads": [...], "scores": {...}}
          marketing nodes write:     {"campaigns": [...], "targeting": {...}}
        """
        state = fresh_state()
        assert state["artifacts"]["workflow_data"] is None
        assert state["artifacts"]["report"] is None

    def test_errors_starts_as_empty_list(self):
        """
        WHAT THIS PROVES: errors must start as a list (not None).
        The append_errors reducer does existing + new — if existing were None,
        that would raise TypeError. Empty list is the correct starting point.
        """
        state = fresh_state()
        assert state["errors"] == []
        assert isinstance(state["errors"], list)

    def test_iteration_count_starts_at_zero(self):
        """
        WHAT THIS PROVES: iteration_count uses operator.add as its reducer.
        Starting at 0 means the first node returning {"iteration_count": 1}
        correctly sets total to 1 (0 + 1 = 1), not overwrite to 1 from unknown base.
        """
        state = fresh_state()
        assert state["iteration_count"] == 0

    def test_status_starts_as_running(self):
        """
        WHAT THIS PROVES: Every workflow starts in "running" state.
        The persist_node at the end transitions this to "completed" or "failed".
        """
        state = fresh_state()
        assert state["status"] == "running"

    def test_approval_gate_defaults_to_not_required(self):
        """
        WHAT THIS PROVES: Approval is OFF by default for all workflows.
        A planner_node detecting constraints["require_approval"] == True
        would flip approval.required = True and approval.status = "pending".

        Any workflow type can gate on approval — supply chain before reordering,
        content pipeline before publishing, lead gen before sending outreach.
        """
        state = fresh_state()
        assert state["approval"]["required"] is False
        assert state["approval"]["status"] == "not_required"

    def test_context_is_none_without_memory(self):
        """
        WHAT THIS PROVES: When no session context is passed, context is None.
        Agents must handle this gracefully (check before reading context fields).
        In Phase 7, context is loaded from Redis/Supabase and injected here.
        """
        state = fresh_state()
        assert state["context"] is None

    def test_metrics_all_start_as_none(self):
        """
        WHAT THIS PROVES: All metrics start as None — they are filled by
        analytics nodes during the run. None means "not yet computed".
        After the run, persist_node reads these and writes them to Supabase.
        """
        state = fresh_state()
        m = state["metrics"]
        assert m["item_count"] is None
        assert m["quality_score"] is None
        assert m["confidence"] is None
        assert m["custom"] is None

    def test_workflow_plan_starts_none(self):
        """
        WHAT THIS PROVES: workflow_plan starts as None — planner_node sets it.
        A single-workflow run gets a one-item list: ["lead_gen"].
        A multi-workflow run gets multiple items: ["research_intelligence", "content_pipeline", "social_publishing"].
        Before planner_node runs, no node should read workflow_plan.
        """
        state = fresh_state()
        assert state["workflow_plan"] is None

    def test_current_workflow_index_starts_at_zero(self):
        """
        WHAT THIS PROVES: current_workflow_index uses operator.add (same as iteration_count).
        Starts at 0. After each workflow completes, workflow_transition_node returns
        {"current_workflow_index": 1} which ADDS 1 (not overwrites to 1).
        workflow_progression_router reads this index to pick the next workflow.
        """
        state = fresh_state()
        assert state["current_workflow_index"] == 0

    def test_multi_workflow_plan_advances_correctly(self):
        """
        WHAT THIS PROVES: A three-workflow chain is just a list of strings.
        The same AgentState schema handles single and multi-workflow runs.
        No schema change needed to add a new workflow to a chain —
        the planner just includes the new workflow_type string in the list.
        """
        import operator
        state = fresh_state()

        # Planner sets the full chain
        state = {**state, "workflow_plan": [
            "research_intelligence",
            "content_pipeline",
            "social_publishing",
        ]}

        assert state["workflow_plan"][0] == "research_intelligence"

        # Simulate workflow_transition_node running after research completes
        index = operator.add(state["current_workflow_index"], 1)
        assert index == 1
        assert state["workflow_plan"][index] == "content_pipeline"

        # Simulate transition after content completes
        index = operator.add(index, 1)
        assert index == 2
        assert state["workflow_plan"][index] == "social_publishing"

        # After social publishing, index goes past end → route to END
        index = operator.add(index, 1)
        assert index == 3
        assert index >= len(state["workflow_plan"])  # triggers "end" route


# ---------------------------------------------------------------------------
# Group 2: Reducer behavior
# The most important tests — teach HOW LangGraph merges partial node returns.
# ---------------------------------------------------------------------------

class TestReducers:
    """
    These tests simulate what LangGraph does when a node returns a partial dict.

    In real LangGraph execution, you never call reducers manually — the compiled
    graph does it. But understanding them here is critical for knowing WHY nodes
    should only return the keys they changed.

    The reducers are WORKFLOW-AGNOSTIC: they work identically for supply chain,
    lead gen, marketing, or any other workflow type.
    """

    def test_append_errors_accumulates_across_nodes(self):
        """
        WHAT THIS PROVES: Errors stack across nodes from ANY workflow.
        Without this, each node's error would overwrite the previous one.

        Real scenario (supply chain workflow):
          fetch_suppliers_node times out → errors += ["fetch_suppliers: db timeout"]
          score_risk_node gets bad data  → errors += ["score_risk: malformed response"]
          Both appear in state["errors"] at the end.
        """
        existing = ["fetch_suppliers: supabase timeout"]
        new_error = ["score_risk: malformed json from gmaps api"]
        result = append_errors(existing, new_error)
        assert result == [
            "fetch_suppliers: supabase timeout",
            "score_risk: malformed json from gmaps api",
        ]

    def test_append_errors_from_empty_start(self):
        """
        WHAT THIS PROVES: The first node to fail adds its error cleanly.
        Starting from [] works correctly with the append reducer.
        """
        result = append_errors([], ["intent_node: failed to parse workflow type"])
        assert result == ["intent_node: failed to parse workflow type"]

    def test_keep_latest_returns_new_value(self):
        """
        WHAT THIS PROVES: When a node writes a new value, it replaces the old.
        This is the default behavior for most state fields.
        """
        result = keep_latest("supply_chain_v1", "supply_chain_v2")
        assert result == "supply_chain_v2"

    def test_keep_latest_ignores_none_preserves_existing(self):
        """
        WHAT THIS PROVES: If a node returns {"metrics": None}, the existing
        metrics are preserved. This protects data computed by earlier nodes.

        Without this: a late-running intent_node returning {"metrics": None}
        would wipe quality scores computed by the analytics_node.

        With this: None is treated as "I didn't touch this" — existing wins.
        """
        existing_metrics = {"item_count": 30, "quality_score": 0.82}
        result = keep_latest(existing_metrics, None)
        assert result == existing_metrics

    def test_iteration_count_accumulates_with_add_reducer(self):
        """
        WHAT THIS PROVES: operator.add means each retry node increments the counter.
        This is how loop detection works in ANY workflow with retry logic.

        In real code, a quality_check_node that fails returns:
            return {"iteration_count": 1}    # adds 1, doesn't set to 1
        The graph checks: if state["iteration_count"] >= MAX_RETRIES → route to fail.

        Without this reducer: every retry would reset count to 1, making it
        impossible to detect infinite loops.
        """
        import operator
        count = operator.add(0, 1)   # first retry
        count = operator.add(count, 1)   # second retry
        count = operator.add(count, 1)   # third retry
        assert count == 3


# ---------------------------------------------------------------------------
# Group 3: Partial update pattern
# Shows the exact dict-return pattern every node uses — for three different
# workflow types, proving the pattern is truly domain-agnostic.
# ---------------------------------------------------------------------------

class TestPartialUpdates:
    """
    These tests simulate what happens when node functions return partial dicts.
    This is the fundamental LangGraph node pattern.

    Key insight: the SAME partial update pattern works for every workflow.
    Nodes for supply chain, marketing, agriculture — all follow the same contract.
    """

    def test_intent_node_pattern_for_supply_chain(self):
        """
        WHAT THIS PROVES: intent_node parses the user_query and returns
        only goal + workflow_type + department + constraints.
        It never touches artifacts, metrics, errors, or any other field.

        This example shows a supply chain workflow being identified.
        """
        state = fresh_state("find 30 high-risk suppliers in East Java")

        # Simulate what intent_node returns for a supply chain query
        intent_update = {
            "goal": "assess_supplier_risk",
            "workflow_type": "supply_chain",
            "department": "analytics",
            "constraints": {
                "limit": 30,
                "require_approval": False,
                "filters": {"region": "East Java", "risk_level": "high"},
            },
        }

        updated = {**state, **intent_update}

        assert updated["workflow_type"] == "supply_chain"
        assert updated["department"] == "analytics"
        assert updated["constraints"]["filters"]["region"] == "East Java"
        # Everything else is untouched
        assert updated["user_query"] == state["user_query"]
        assert updated["artifacts"]["workflow_data"] is None
        assert updated["errors"] == []

    def test_intent_node_pattern_for_marketing(self):
        """
        WHAT THIS PROVES: The SAME intent_node pattern works for a completely
        different client use case (marketing campaigns vs supply chain).
        Same state schema, same return pattern, different workflow_type string.
        """
        state = fresh_state("generate 5 email campaigns for our premium segment")

        intent_update = {
            "goal": "generate_campaigns",
            "workflow_type": "marketing",
            "department": "content",
            "constraints": {
                "limit": 5,
                "require_approval": True,  # marketing needs approval before send
                "filters": {"segment": "premium", "channel": "email"},
            },
        }

        updated = {**state, **intent_update}

        assert updated["workflow_type"] == "marketing"
        assert updated["department"] == "content"
        assert updated["constraints"]["require_approval"] is True
        assert updated["artifacts"]["workflow_data"] is None  # not yet

    def test_fetch_node_writes_into_workflow_data(self):
        """
        WHAT THIS PROVES: The fetch_node for ANY workflow writes its raw
        output into artifacts["workflow_data"]. The structure inside
        workflow_data is workflow-specific — but the update pattern is identical.

        This example shows a supply chain fetch_node writing raw suppliers.
        A lead_gen fetch_node would write leads instead — same pattern.
        """
        state = fresh_state()
        state = {**state, "workflow_type": "supply_chain", "department": "analytics"}

        raw_suppliers = [
            {"id": "s1", "name": "PT Maju Jaya", "region": "East Java", "audit_score": 0.45},
            {"id": "s2", "name": "CV Sumber Makmur", "region": "East Java", "audit_score": 0.61},
        ]

        # fetch_node updates artifacts and metrics — nothing else
        updated_artifacts = {**state["artifacts"], "workflow_data": {"suppliers": raw_suppliers}}
        updated_metrics = {**state["metrics"], "item_count": len(raw_suppliers)}

        fetch_update = {"artifacts": updated_artifacts, "metrics": updated_metrics}
        updated = {**state, **fetch_update}

        assert len(updated["artifacts"]["workflow_data"]["suppliers"]) == 2
        assert updated["metrics"]["item_count"] == 2
        assert updated["artifacts"]["report"] is None  # report_node hasn't run yet

    def test_analytics_node_enriches_workflow_data(self):
        """
        WHAT THIS PROVES: Each node reads the PREVIOUS node's output from
        workflow_data and adds its own result back — the pipeline pattern.
        No node overwrites what came before; it spreads and extends.

        This simulates a risk_scoring_node reading raw suppliers and adding
        risk scores without deleting the original supplier data.
        """
        state = fresh_state()
        raw_suppliers = [
            {"id": "s1", "name": "PT Maju Jaya", "audit_score": 0.45},
            {"id": "s2", "name": "CV Sumber Makmur", "audit_score": 0.61},
        ]

        # Pre-populate as if fetch_node ran
        state = {**state, "artifacts": {
            **state["artifacts"],
            "workflow_data": {"suppliers": raw_suppliers}
        }}

        assert state["artifacts"]["workflow_data"] is not None  # safe to proceed

        # risk_scoring_node reads suppliers, writes risk_map back
        risk_map = {"s1": "high", "s2": "medium"}
        scoring_update = {
            "artifacts": {
                **state["artifacts"],
                "workflow_data": {
                    **state["artifacts"]["workflow_data"],
                    "risk_map": risk_map,        # new key added
                    # "suppliers" is preserved via spread above
                },
            },
            "metrics": {
                **state["metrics"],
                "quality_score": 0.88,
                "custom": {"high_risk_count": 1},
            },
        }
        updated = {**state, **scoring_update}

        # risk_map added
        assert updated["artifacts"]["workflow_data"]["risk_map"]["s1"] == "high"
        # original suppliers preserved
        assert len(updated["artifacts"]["workflow_data"]["suppliers"]) == 2
        assert updated["metrics"]["quality_score"] == 0.88

    def test_error_accumulation_across_multiple_nodes(self):
        """
        WHAT THIS PROVES: When multiple nodes fail in any workflow,
        ALL errors are preserved via the append_errors reducer.

        In production you inspect state["errors"] at the end to decide:
        retry, alert on Slack, or fail gracefully.
        """
        state = fresh_state()

        # Simulating two sequential node failures
        state = {**state, "errors": append_errors(state["errors"], ["fetch_suppliers: db timeout"])}
        state = {**state, "errors": append_errors(state["errors"], ["score_risk: openai rate limit"])}

        assert len(state["errors"]) == 2
        assert "fetch_suppliers: db timeout" in state["errors"]
        assert "score_risk: openai rate limit" in state["errors"]


# ---------------------------------------------------------------------------
# Group 4: Memory layer
# Tests that context flows correctly from Supabase/Redis into AgentState.
# ---------------------------------------------------------------------------

class TestMemoryLayer:

    def test_state_with_supply_chain_context_injected(self):
        """
        WHAT THIS PROVES: Domain-specific context from Redis/Supabase can be
        injected at state creation for any workflow type.

        The supply_chain domain_context contains approved suppliers and risk
        weights — things that live in the DB, not in the prompt.
        Agents access this as state["context"]["domain_context"]["risk_weights"].
        """
        context = supply_chain_context()
        state = make_initial_state(
            user_query="find high-risk suppliers",
            run_id=str(uuid4()),
            context=context,
        )

        assert state["context"] is not None
        assert state["context"]["benchmark_score"] == 0.74
        assert len(state["context"]["past_run_summaries"]) == 2
        assert "risk_weights" in state["context"]["domain_context"]

    def test_memory_context_loader_generic_conversion(self):
        """
        WHAT THIS PROVES: MemoryContextLoader.to_agent_context() works for any
        workflow. The same loader handles supply chain, marketing, agriculture —
        domain specifics go into domain_context.
        """
        loader = MemoryContextLoader(
            recent_summaries=["Last run: 23 suppliers flagged"],
            benchmark_score=0.74,
            user_preferences={"alert_threshold": "high"},
            domain_context={"approved_suppliers": ["PT Maju Jaya"]},
        )
        ctx = loader.to_agent_context()

        assert ctx["past_run_summaries"] == ["Last run: 23 suppliers flagged"]
        assert ctx["benchmark_score"] == 0.74
        assert ctx["domain_context"]["approved_suppliers"] == ["PT Maju Jaya"]

    def test_empty_loader_returns_none_fields(self):
        """
        WHAT THIS PROVES: A first-time user has no history — all context fields
        are None. Agents must check for None before accessing context fields.
        This is true for any workflow type.
        """
        loader = MemoryContextLoader()
        ctx = loader.to_agent_context()

        assert ctx["past_run_summaries"] is None
        assert ctx["benchmark_score"] is None
        assert ctx["domain_context"] is None

    def test_workflow_run_record_for_supply_chain(self):
        """
        WHAT THIS PROVES: WorkflowRunRecord stores run metadata for ANY workflow.
        metadata holds workflow-specific data without schema changes.

        persist_node writes one of these at the end of every graph run.
        """
        record = WorkflowRunRecord(
            run_id="run-sc-001",
            workflow_type="supply_chain",
            status="completed",
            item_count=30,
            quality_score=0.88,
            iteration_count=1,
            metadata={"region": "East Java", "high_risk_count": 7},
        )

        assert record.workflow_type == "supply_chain"
        assert record.metadata["high_risk_count"] == 7
        assert record.errors == []  # empty by default

    def test_workflow_run_record_for_lead_gen(self):
        """
        WHAT THIS PROVES: The SAME WorkflowRunRecord works for lead_gen.
        workflow_type is an open string — no enum, no schema migration.
        """
        record = WorkflowRunRecord(
            run_id="run-lg-002",
            workflow_type="lead_gen",
            status="completed",
            item_count=50,
            quality_score=0.91,
            iteration_count=2,
            metadata={"location": "Jakarta", "category": "pharmaceutical"},
        )

        assert record.workflow_type == "lead_gen"
        assert record.item_count == 50
        assert record.metadata["category"] == "pharmaceutical"

    def test_workflow_artifact_record_generic(self):
        """
        WHAT THIS PROVES: WorkflowArtifactRecord stores any structured output.
        artifact_type is a string label; data is any dict.
        embedding stays None until we add the pgvector embedding node.

        Different workflows use different artifact_types:
          supply_chain: "risk_assessment", "supplier_report"
          lead_gen:     "leads_batch", "outreach_drafts"
          marketing:    "campaign_brief"
        """
        record = WorkflowArtifactRecord(
            run_id="run-sc-001",
            artifact_type="risk_assessment",
            data={
                "suppliers": [{"id": "s1", "name": "PT Maju Jaya"}],
                "risk_map": {"s1": "high"},
                "recommendations": ["Audit PT Maju Jaya within 30 days"],
            },
        )

        assert record.artifact_type == "risk_assessment"
        assert record.data["risk_map"]["s1"] == "high"
        assert record.embedding is None  # not yet — added in later phase


# ---------------------------------------------------------------------------
# Group 5: Guard rails
# Tests that must pass for every workflow type, every time.
# ---------------------------------------------------------------------------

class TestGuardRails:

    def test_run_id_is_always_a_string(self):
        """
        WHAT THIS PROVES: run_id is the LangGraph thread_id and the API
        workflow identifier. It must always be a non-empty string.
        """
        run_id = str(uuid4())
        state = make_initial_state("any query", run_id=run_id)
        assert isinstance(state["run_id"], str)
        assert len(state["run_id"]) > 0

    def test_make_initial_state_is_workflow_agnostic(self):
        """
        WHAT THIS PROVES: make_initial_state() works identically regardless of
        what workflow_type will eventually be set. The initial state is identical
        for all workflows — intent_node + planner_node differentiate them later.
        """
        s1 = make_initial_state("find 50 suppliers", run_id="run-a")
        s2 = make_initial_state("generate 5 campaigns", run_id="run-b")
        s3 = make_initial_state("analyze crop yields", run_id="run-c")

        # All start identically (except run_id and user_query)
        assert s1["workflow_type"] == s2["workflow_type"] == s3["workflow_type"] is None
        assert s1["department"] == s2["department"] == s3["department"] is None
        assert s1["status"] == s2["status"] == s3["status"] == "running"
        assert s1["errors"] == s2["errors"] == s3["errors"] == []
        assert s1["iteration_count"] == 0

    def test_workflow_progression_router_advances_correctly(self):
        """
        WHAT THIS PROVES: workflow_progression_router reads workflow_plan[current_index]
        and returns the next workflow_type string. When index >= len(plan) it returns "end".
        This is the router that powers multi-workflow chains.
        """
        from app.graph.conditions import workflow_progression_router

        state = fresh_state()
        state = {**state, "workflow_plan": [
            "research_intelligence",
            "content_pipeline",
            "social_publishing",
        ], "current_workflow_index": 0}

        assert workflow_progression_router(state) == "research_intelligence"

        state = {**state, "current_workflow_index": 2}
        assert workflow_progression_router(state) == "social_publishing"

        state = {**state, "current_workflow_index": 3}
        assert workflow_progression_router(state) == "end"   # past end of plan

    def test_adding_new_workflow_type_needs_no_state_change(self):
        """
        WHAT THIS PROVES: workflow_type is an open string — adding "agriculture"
        or "hr_pipeline" or any new client use case requires ZERO changes to
        AgentState. You just use the new string.

        This is the core extensibility principle of the system.
        """
        # No code change to state.py needed for "agriculture" workflow
        state = make_initial_state("audit crop yield reports for Q1", run_id="run-ag-001")

        # intent_node would set this — the state accepts any string
        update = {"workflow_type": "agriculture", "department": "operations"}
        updated = {**state, **update}

        assert updated["workflow_type"] == "agriculture"
        assert updated["department"] == "operations"


# ---------------------------------------------------------------------------
# Group 6: Message bus (A2A)
# Tests the agent-to-agent communication layer.
# ---------------------------------------------------------------------------

class TestMessageBus:
    """
    Proves the A2A message bus pattern: agents communicate by writing
    structured messages into state["messages"], never by calling each other.

    The message_router reads pending messages and routes to the correct agent.
    Agents use message_bus.py helpers — they never touch state["messages"] raw.
    """

    def test_messages_starts_as_empty_list(self):
        """
        WHAT THIS PROVES: state["messages"] starts as [] at run start.
        No messages in flight until an agent explicitly sends one.
        Agents that check get_pending() at the start of a run safely get [].
        """
        state = fresh_state()
        assert state["messages"] == []
        assert isinstance(state["messages"], list)

    def test_append_messages_reducer_stacks_from_two_agents(self):
        """
        WHAT THIS PROVES: The append_messages reducer works like append_errors —
        when two agents both send messages in the same graph step, BOTH are kept.

        Without this reducer: agent B's send would overwrite agent A's.
        With this reducer: both messages are on the bus.

        This is the key difference between append_messages and keep_latest.
        """
        from app.state import append_messages

        msg_from_content = [{"id": "m1", "from_agent": "content_agent",
                              "to_agent": "analytics_agent", "task": "analyze_video",
                              "status": "pending"}]
        msg_from_analytics = [{"id": "m2", "from_agent": "analytics_agent",
                                "to_agent": "distribution_agent", "task": "schedule_post",
                                "status": "pending"}]

        result = append_messages(msg_from_content, msg_from_analytics)
        assert len(result) == 2
        assert result[0]["id"] == "m1"
        assert result[1]["id"] == "m2"

    def test_send_message_enqueues_pending_message(self):
        """
        WHAT THIS PROVES: send_message() creates a valid pending message
        and returns a state update dict with only the new message in a list.
        The from_agent, to_agent, task, payload, status, and provenance are set.

        Agents use this instead of constructing message dicts manually.
        """
        from app.agents.shared.message_bus import send_message

        state = fresh_state()
        update = send_message(
            state,
            from_agent="content_agent",
            to_agent="analytics_agent",
            task="analyze_video",
            payload={"video_url": "https://cdn.example.com/v1.mp4"},
        )

        assert "messages" in update
        assert len(update["messages"]) == 1
        msg = update["messages"][0]
        assert msg["from_agent"] == "content_agent"
        assert msg["to_agent"] == "analytics_agent"
        assert msg["task"] == "analyze_video"
        assert msg["status"] == "pending"
        assert msg["provenance"] == "inter_session"
        assert msg["result"] is None
        assert "id" in msg
        assert "created_at" in msg

    def test_mark_done_updates_status_and_result(self):
        """
        WHAT THIS PROVES: mark_done() updates only the target message's status
        and result. All other messages in the bus are preserved unchanged.

        This is the "complete your task and report back" pattern.
        analytics_agent reads its pending message, processes it, calls mark_done.
        The bus now shows the message as done with the result attached.
        """
        from app.agents.shared.message_bus import mark_done

        state = fresh_state()
        # Pre-populate bus with two messages
        state = {**state, "messages": [
            {"id": "m1", "from_agent": "content_agent", "to_agent": "analytics_agent",
             "task": "analyze_video", "status": "pending", "result": None,
             "payload": {"url": "https://..."}, "provenance": "inter_session",
             "created_at": "2026-01-01T00:00:00+00:00"},
            {"id": "m2", "from_agent": "planner_node", "to_agent": "content_agent",
             "task": "generate_script", "status": "pending", "result": None,
             "payload": {"topic": "AI trends"}, "provenance": "inter_session",
             "created_at": "2026-01-01T00:00:01+00:00"},
        ]}

        analysis_result = {"engagement_score": 0.87, "recommendation": "publish"}
        update = mark_done(state, msg_id="m1", result=analysis_result)

        updated_messages = update["messages"]
        assert len(updated_messages) == 2  # both messages preserved

        done_msg = next(m for m in updated_messages if m["id"] == "m1")
        assert done_msg["status"] == "done"
        assert done_msg["result"]["engagement_score"] == 0.87

        # Other message untouched
        other_msg = next(m for m in updated_messages if m["id"] == "m2")
        assert other_msg["status"] == "pending"
        assert other_msg["result"] is None

    def test_message_router_returns_to_agent_for_first_pending(self):
        """
        WHAT THIS PROVES: message_router scans the bus and returns the to_agent
        of the FIRST pending message. This is what the graph uses to route
        to the correct agent node.

        The graph maps the returned string to a node:
          {"analytics_agent": "analytics_node", "end": END}
        """
        from app.graph.conditions import message_router

        state = fresh_state()
        state = {**state, "messages": [
            {"id": "m1", "from_agent": "content_agent", "to_agent": "analytics_agent",
             "task": "analyze_video", "status": "pending", "result": None},
            {"id": "m2", "from_agent": "analytics_agent", "to_agent": "distribution_agent",
             "task": "schedule_post", "status": "pending", "result": None},
        ]}

        # Returns first pending message's to_agent
        assert message_router(state) == "analytics_agent"

    def test_message_router_returns_end_when_no_pending(self):
        """
        WHAT THIS PROVES: When all messages are done/failed (or bus is empty),
        message_router returns "end" — signalling the graph that all A2A work
        is complete and the workflow can proceed to the next step.

        This is the exit condition for the A2A dispatch loop.
        """
        from app.graph.conditions import message_router

        state = fresh_state()
        # Bus is empty
        assert message_router(state) == "end"

        # Bus has only completed messages
        state = {**state, "messages": [
            {"id": "m1", "from_agent": "a", "to_agent": "b",
             "task": "t", "status": "done", "result": {"ok": True}},
        ]}
        assert message_router(state) == "end"
