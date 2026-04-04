"""
tests/test_intent_planner.py — Unit tests for intent_node and planner_node.

HOW TO RUN (from project/backend/):
    python -m pytest tests/test_intent_planner.py -v

WHAT YOU WILL LEARN:
  These tests validate Phase 3 without needing a live LLM:
    1. Internal helpers — constraint normalization, plan normalization, goal slugging
    2. State update shapes — what each node writes and what it leaves untouched
    3. Fallback behavior — graceful degradation when LLM returns bad JSON
    4. Registry integration — unknown workflow types are dropped from plans
    5. Meta-graph compilation — build_meta_graph() compiles without errors in Phase 3

  The tests mock call_llm() so the full node functions can be exercised
  without an OpenAI API key.
"""

import json
import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.state import make_initial_state
from app.agents.shared.intent_node import (
    _safe_goal,
    _normalize_constraints,
    _fallback_intent,
)
from app.agents.shared.planner_node import (
    _fallback_plan,
    _normalize_plan,
    _build_available_agents,
)
from app.agents.shared.llm_client import render_prompt, parse_json_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fresh_state(query: str = "find 30 suppliers in East Java") -> dict:
    return make_initial_state(user_query=query, run_id=str(uuid4()))


# ---------------------------------------------------------------------------
# Group 1: llm_client helpers
# ---------------------------------------------------------------------------

class TestLlmClientHelpers:

    def test_render_prompt_fills_placeholders(self):
        """
        WHAT THIS PROVES: render_prompt correctly substitutes {{ variable }} tokens.
        """
        template = "Hello {{ name }}, your query is: {{ query }}"
        result = render_prompt(template, name="Alice", query="find suppliers")
        assert result == "Hello Alice, your query is: find suppliers"

    def test_render_prompt_leaves_unmatched_placeholder(self):
        """
        WHAT THIS PROVES: Unknown placeholders are left as-is without error.
        """
        template = "Value: {{ known }} and {{ unknown }}"
        result = render_prompt(template, known="hello")
        assert "hello" in result
        assert "{{ unknown }}" in result

    def test_render_prompt_none_value_becomes_empty_string(self):
        """
        WHAT THIS PROVES: None values produce empty strings (not the string "None").
        """
        template = "Context: {{ ctx }}"
        result = render_prompt(template, ctx=None)
        assert result == "Context: "

    def test_parse_json_response_plain_json(self):
        """
        WHAT THIS PROVES: Straightforward JSON parses correctly.
        """
        raw = '{"goal": "find_leads", "complexity": "single_workflow"}'
        result = parse_json_response(raw)
        assert result["goal"] == "find_leads"

    def test_parse_json_response_strips_code_fence(self):
        """
        WHAT THIS PROVES: LLM-wrapped JSON (```json ... ```) is unwrapped before parsing.
        """
        raw = '```json\n{"goal": "test", "complexity": "direct"}\n```'
        result = parse_json_response(raw)
        assert result["goal"] == "test"

    def test_parse_json_response_raises_on_bad_json(self):
        """
        WHAT THIS PROVES: Malformed JSON raises ValueError with a readable message.
        """
        with pytest.raises(ValueError, match="LLM returned invalid JSON"):
            parse_json_response("not valid json { }", context="test_node")


# ---------------------------------------------------------------------------
# Group 2: intent_node helpers
# ---------------------------------------------------------------------------

class TestIntentNodeHelpers:

    def test_safe_goal_slugifies_query(self):
        """
        WHAT THIS PROVES: _safe_goal produces a clean snake_case goal slug.
        """
        result = _safe_goal("Find 50 pharmaceutical leads in Surabaya!")
        assert result == "find_50_pharmaceutical_leads_in"

    def test_safe_goal_empty_query_returns_unknown(self):
        result = _safe_goal("")
        assert result == "unknown_goal"

    def test_normalize_constraints_full(self):
        """
        WHAT THIS PROVES: Well-formed constraints from the LLM pass through correctly.
        """
        raw = {"limit": 30, "require_approval": False, "filters": {"region": "East Java"}}
        result = _normalize_constraints(raw)
        assert result["limit"] == 30
        assert result["require_approval"] is False
        assert result["filters"]["region"] == "East Java"

    def test_normalize_constraints_null_limit(self):
        """
        WHAT THIS PROVES: JSON null (Python None) for limit is preserved as None.
        """
        raw = {"limit": None, "require_approval": False, "filters": {}}
        result = _normalize_constraints(raw)
        assert result["limit"] is None

    def test_normalize_constraints_missing_keys(self):
        """
        WHAT THIS PROVES: Partially missing keys get safe defaults without raising.
        """
        result = _normalize_constraints({})
        assert result["limit"] is None
        assert result["require_approval"] is False
        assert result["filters"] == {}

    def test_normalize_constraints_non_dict_input(self):
        """
        WHAT THIS PROVES: Non-dict input (e.g. LLM returned a string) gets default values.
        """
        result = _normalize_constraints("bad input")
        assert result["limit"] is None
        assert result["require_approval"] is False

    def test_normalize_constraints_string_limit_coerced_to_int(self):
        """
        WHAT THIS PROVES: String numbers like "50" are coerced to int.
        """
        raw = {"limit": "50", "require_approval": False, "filters": {}}
        result = _normalize_constraints(raw)
        assert result["limit"] == 50

    def test_fallback_intent_produces_valid_structure(self):
        """
        WHAT THIS PROVES: The fallback path always produces a parseable dict
        with the same shape as the normal LLM response, ensuring planner_node
        never receives an unexpected structure.
        """
        result = _fallback_intent("find suppliers in East Java")
        assert result["complexity"] == "direct"
        assert isinstance(result["suggested_workflows"], list)
        assert "limit" in result["constraints"]
        assert "require_approval" in result["constraints"]
        assert "filters" in result["constraints"]


# ---------------------------------------------------------------------------
# Group 3: planner_node helpers
# ---------------------------------------------------------------------------

class TestPlannerNodeHelpers:

    KNOWN_TYPES = {"lead_gen", "supply_chain", "research_intelligence", "content_pipeline"}

    def test_normalize_plan_direct(self):
        """
        WHAT THIS PROVES: direct complexity always produces ["direct_answer"] regardless
        of what the LLM put in workflow_plan.
        """
        raw = {"complexity": "direct", "workflow_plan": ["lead_gen"], "department_sequence": ["distribution"]}
        result = _normalize_plan(raw, self.KNOWN_TYPES)
        assert result["workflow_plan"] == ["direct_answer"]
        assert result["department_sequence"] == ["distribution"]

    def test_normalize_plan_single_workflow_trims_to_one(self):
        """
        WHAT THIS PROVES: single_workflow complexity trims the plan to exactly one entry.
        The LLM sometimes lists extra workflows even for single complexity.
        """
        raw = {
            "complexity": "single_workflow",
            "workflow_plan": ["lead_gen", "supply_chain"],
            "department_sequence": ["distribution", "analytics"],
        }
        result = _normalize_plan(raw, self.KNOWN_TYPES)
        assert result["workflow_plan"] == ["lead_gen"]
        assert result["department_sequence"] == ["distribution"]

    def test_normalize_plan_unknown_workflow_types_dropped(self):
        """
        WHAT THIS PROVES: workflow types not registered in the registry are dropped.
        This prevents routing errors from hallucinated workflow names.
        """
        raw = {
            "complexity": "multi_workflow",
            "workflow_plan": ["lead_gen", "hallucinated_workflow", "supply_chain"],
            "department_sequence": ["distribution", "unknown", "analytics"],
        }
        result = _normalize_plan(raw, self.KNOWN_TYPES)
        assert "hallucinated_workflow" not in result["workflow_plan"]
        assert "lead_gen" in result["workflow_plan"]
        assert "supply_chain" in result["workflow_plan"]

    def test_normalize_plan_direct_answer_always_allowed(self):
        """
        WHAT THIS PROVES: "direct_answer" is a valid pseudo-workflow even though
        it is not registered in the YAML registry.
        """
        raw = {
            "complexity": "single_workflow",
            "workflow_plan": ["direct_answer"],
            "department_sequence": ["shared"],
        }
        result = _normalize_plan(raw, self.KNOWN_TYPES)
        assert result["workflow_plan"] == ["direct_answer"]

    def test_normalize_plan_pads_short_department_sequence(self):
        """
        WHAT THIS PROVES: When department_sequence is shorter than workflow_plan,
        it is padded with the last known department (or "shared" if empty).
        This ensures len(department_sequence) == len(workflow_plan) always holds.
        """
        raw = {
            "complexity": "multi_workflow",
            "workflow_plan": ["research_intelligence", "content_pipeline", "lead_gen"],
            "department_sequence": ["research"],  # only one entry — needs padding
        }
        result = _normalize_plan(raw, self.KNOWN_TYPES)
        assert len(result["department_sequence"]) == len(result["workflow_plan"])
        assert result["department_sequence"][1] == "research"  # padded with last
        assert result["department_sequence"][2] == "research"

    def test_normalize_plan_invalid_complexity_falls_back_to_direct(self):
        """
        WHAT THIS PROVES: Unknown complexity strings default to "direct" safely.
        """
        raw = {
            "complexity": "invented_complexity",
            "workflow_plan": ["lead_gen"],
            "department_sequence": ["distribution"],
        }
        result = _normalize_plan(raw, self.KNOWN_TYPES)
        assert result["complexity"] == "direct"
        assert result["workflow_plan"] == ["direct_answer"]

    def test_fallback_plan_with_workflow_type(self):
        """
        WHAT THIS PROVES: Fallback with a known workflow_type produces a valid
        single_workflow plan using that type.
        """
        result = _fallback_plan("lead_gen")
        assert result["complexity"] == "single_workflow"
        assert result["workflow_plan"] == ["lead_gen"]
        assert len(result["department_sequence"]) == 1

    def test_fallback_plan_without_workflow_type(self):
        """
        WHAT THIS PROVES: Fallback with no workflow_type produces a direct plan.
        """
        result = _fallback_plan(None)
        assert result["complexity"] == "direct"
        assert result["workflow_plan"] == ["direct_answer"]

    def test_build_available_agents_no_workflows(self):
        """
        WHAT THIS PROVES: _build_available_agents handles an empty list gracefully.
        """
        result = _build_available_agents([])
        assert "no agents registered" in result


# ---------------------------------------------------------------------------
# Group 4: intent_node full flow (LLM mocked)
# ---------------------------------------------------------------------------

class TestIntentNodeFull:

    @pytest.mark.asyncio
    async def test_intent_node_single_workflow_happy_path(self):
        """
        WHAT THIS PROVES: When the LLM returns valid JSON, intent_node correctly
        sets goal, workflow_type, and constraints in the state update.
        """
        from app.agents.shared.intent_node import intent_node

        llm_response = json.dumps({
            "goal": "find_coffee_suppliers",
            "complexity": "single_workflow",
            "suggested_workflows": ["lead_gen"],
            "reasoning": "Single operational task.",
            "constraints": {"limit": 30, "require_approval": False, "filters": {"category": "coffee"}},
        })

        state = fresh_state("Find 30 coffee suppliers in East Java")

        with patch("app.agents.shared.intent_node.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.agents.shared.intent_node.get_all_workflow_types", return_value=["lead_gen", "supply_chain"]):
            mock_llm.return_value = llm_response
            result = await intent_node(state)

        assert result["goal"] == "find_coffee_suppliers"
        assert result["workflow_type"] == "lead_gen"
        assert result["constraints"]["limit"] == 30
        assert result["constraints"]["filters"]["category"] == "coffee"
        # workflow_plan should NOT be set for single_workflow
        assert "workflow_plan" not in result

    @pytest.mark.asyncio
    async def test_intent_node_multi_workflow_sets_plan(self):
        """
        WHAT THIS PROVES: multi_workflow complexity causes intent_node to set
        workflow_plan with the suggested workflows as a seed for planner_node.
        """
        from app.agents.shared.intent_node import intent_node

        llm_response = json.dumps({
            "goal": "research_create_publish",
            "complexity": "multi_workflow",
            "suggested_workflows": ["research_intelligence", "content_pipeline", "social_publishing"],
            "reasoning": "Three distinct stages.",
            "constraints": {"limit": None, "require_approval": True, "filters": {}},
        })

        state = fresh_state("Research trends, write a script, and post to TikTok")

        with patch("app.agents.shared.intent_node.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.agents.shared.intent_node.get_all_workflow_types",
                   return_value=["research_intelligence", "content_pipeline", "social_publishing"]):
            mock_llm.return_value = llm_response
            result = await intent_node(state)

        assert result["workflow_type"] == "research_intelligence"
        assert result["workflow_plan"] == ["research_intelligence", "content_pipeline", "social_publishing"]
        assert result["constraints"]["require_approval"] is True

    @pytest.mark.asyncio
    async def test_intent_node_fallback_on_bad_json(self):
        """
        WHAT THIS PROVES: When the LLM returns malformed JSON, intent_node falls
        back to a "direct" classification and records the error in state["errors"].
        The node never raises — it always returns a usable state update.
        """
        from app.agents.shared.intent_node import intent_node

        state = fresh_state("What is today's exchange rate?")

        with patch("app.agents.shared.intent_node.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.agents.shared.intent_node.get_all_workflow_types", return_value=[]):
            mock_llm.return_value = "not valid json {{ broken"
            result = await intent_node(state)

        assert "errors" in result
        assert any("intent_node" in e for e in result["errors"])
        # Must still produce a usable goal even on failure
        assert "goal" in result
        assert result["workflow_type"] is None

    @pytest.mark.asyncio
    async def test_intent_node_drops_unknown_workflow_types(self):
        """
        WHAT THIS PROVES: workflow types suggested by the LLM but not in the
        registry are silently dropped. workflow_type becomes None.
        """
        from app.agents.shared.intent_node import intent_node

        llm_response = json.dumps({
            "goal": "do_something",
            "complexity": "single_workflow",
            "suggested_workflows": ["hallucinated_workflow"],
            "reasoning": "test",
            "constraints": {"limit": None, "require_approval": False, "filters": {}},
        })

        state = fresh_state("Do something")

        with patch("app.agents.shared.intent_node.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.agents.shared.intent_node.get_all_workflow_types", return_value=["lead_gen"]):
            mock_llm.return_value = llm_response
            result = await intent_node(state)

        assert result["workflow_type"] is None  # hallucinated_workflow was dropped


# ---------------------------------------------------------------------------
# Group 5: planner_node full flow (LLM mocked)
# ---------------------------------------------------------------------------

class TestPlannerNodeFull:

    @pytest.mark.asyncio
    async def test_planner_node_single_workflow(self):
        """
        WHAT THIS PROVES: planner_node produces a validated workflow_plan and
        department for a single workflow query.
        """
        from app.agents.shared.planner_node import planner_node

        llm_response = json.dumps({
            "complexity": "single_workflow",
            "workflow_plan": ["lead_gen"],
            "department_sequence": ["distribution"],
            "reasoning": "Single lead generation task.",
            "estimated_steps": 5,
        })

        state = fresh_state("Find 30 leads")
        state = {**state, "goal": "find_leads", "workflow_type": "lead_gen"}

        with patch("app.agents.shared.planner_node.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.agents.shared.planner_node.get_all_workflow_types", return_value=["lead_gen"]):
            mock_llm.return_value = llm_response
            result = await planner_node(state)

        assert result["workflow_plan"] == ["lead_gen"]
        assert result["department"] == "distribution"
        assert "errors" not in result or result.get("errors") == []

    @pytest.mark.asyncio
    async def test_planner_node_never_sets_current_workflow_index(self):
        """
        WHAT THIS PROVES: planner_node never writes current_workflow_index.
        That counter is exclusively controlled by workflow_transition_node.
        """
        from app.agents.shared.planner_node import planner_node

        llm_response = json.dumps({
            "complexity": "single_workflow",
            "workflow_plan": ["lead_gen"],
            "department_sequence": ["distribution"],
            "reasoning": "Single task.",
            "estimated_steps": 3,
        })

        state = fresh_state("Find leads")
        state = {**state, "goal": "find_leads", "workflow_type": "lead_gen"}

        with patch("app.agents.shared.planner_node.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.agents.shared.planner_node.get_all_workflow_types", return_value=["lead_gen"]):
            mock_llm.return_value = llm_response
            result = await planner_node(state)

        assert "current_workflow_index" not in result

    @pytest.mark.asyncio
    async def test_planner_node_fallback_on_bad_json(self):
        """
        WHAT THIS PROVES: planner_node falls back to workflow_type from state
        and records the error — never raises or produces an unusable state.
        """
        from app.agents.shared.planner_node import planner_node

        state = fresh_state("Find leads")
        state = {**state, "goal": "find_leads", "workflow_type": "lead_gen"}

        with patch("app.agents.shared.planner_node.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("app.agents.shared.planner_node.get_all_workflow_types", return_value=["lead_gen"]):
            mock_llm.return_value = "not json"
            result = await planner_node(state)

        assert "errors" in result
        assert result["workflow_plan"] == ["lead_gen"]
        assert result["department"] is not None


# ---------------------------------------------------------------------------
# Group 6: meta-graph compilation
# ---------------------------------------------------------------------------

class TestMetaGraphCompilation:

    def test_build_meta_graph_compiles_with_empty_subgraphs(self):
        """
        WHAT THIS PROVES: build_meta_graph() compiles and the meta-orchestrator
        wires correctly with no workflow subgraphs registered. The orchestration
        skeleton (intent → planner → direct_answer → END) must work on its own,
        independently of any domain workflow implementations.

        Requires: langgraph (skipped automatically if not installed).
        Run from the project venv which has all dependencies.
        """
        pytest.importorskip("langgraph", reason="langgraph not installed in this environment")

        import app.graph.base_graph as bgm

        # Pre-set to empty dict (not None) to skip the lazy import of workflow builders.
        original = bgm.WORKFLOW_SUBGRAPHS
        bgm.WORKFLOW_SUBGRAPHS = {}
        try:
            graph = bgm.build_meta_graph()
            assert graph is not None
        finally:
            bgm.WORKFLOW_SUBGRAPHS = original  # restore for subsequent tests

    def test_workflow_transition_node_returns_add_one(self):
        """
        WHAT THIS PROVES: workflow_transition_node returns exactly {"current_workflow_index": 1}.
        The operator.add reducer in state.py ensures this ADDS 1, not overwrites.
        """
        from app.graph.base_graph import workflow_transition_node

        result = workflow_transition_node({})
        assert result == {"current_workflow_index": 1}
