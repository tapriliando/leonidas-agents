# Planner design — how the system decides what to run

## Short answer

Yes — the planner can decide between one agent, one workflow, or many workflows.
But its intelligence is **not magic**. It comes from three things you control:

| What drives planner intelligence | Who controls it |
|---|---|
| The `purpose` fields in your registry YAML files | **You** |
| The prompt templates in `backend/app/prompts/` | Developer |
| The LLM model used (GPT-4o, Claude, etc.) | Config |

**The richer your YAML `purpose` descriptions, the better the planner decides.**

---

## The two-node decision pipeline

The system uses **two nodes** to plan — not one. This is intentional.

```
user_query
    ↓
[intent_node]           → classifies: "direct" | "single_workflow" | "multi_workflow"
                          extracts: goal, constraints, suggested_workflows
    ↓
[planner_node]          → builds the final ExecutionPlan
                          produces: workflow_plan, department_sequence, reasoning
    ↓
[meta-orchestrator]     → executes the plan
```

Splitting intent from planning avoids overloading one prompt. The intent node
focuses on understanding; the planner focuses on choosing the right tools.

---

## The three complexity levels

### Level 1 — Direct (one agent)

```
User: "What's the export duty for coffee to Japan?"
Plan: workflow_plan = ["direct_answer"]
      complexity = "direct"
```

A single `answer_node` runs. It calls `mcp.rag_query` or `mcp.web_search`,
returns an answer, and the run is done. No multi-step sequence needed.

### Level 2 — Single workflow (one department)

```
User: "Find 50 pharmaceutical leads in Surabaya"
Plan: workflow_plan = ["lead_gen"]
      complexity = "single_workflow"
      department_sequence = ["distribution"]
```

One workflow, one department. The lead_gen workflow runs its full sequence
(fetch → score → draft → persist). Typical for operational tasks.

### Level 3 — Multi-workflow (cross-department)

```
User: "Research trending topics, write a video about it, post to TikTok"
Plan: workflow_plan = ["research_intelligence", "content_pipeline", "social_publishing"]
      complexity = "multi_workflow"
      department_sequence = ["research", "content", "distribution"]
```

Three workflows across three departments. Output of each feeds into the next
through `artifacts.workflow_data`.

---

## How the planner reads the registry

In the node implementation (Phase 3), the planner calls these registry functions
and injects the results into the prompt template:

```python
from app.registry import format_workflows_for_intent, format_agents_for_planner

# Injected into intent_node.txt as {{ available_workflows }}
workflows_context = format_workflows_for_intent()

# Injected into planner_node.txt as {{ available_agents }}
agents_context = format_agents_for_planner(workflow_type)
```

The prompt template (`backend/app/prompts/planner_node.txt`) then receives:

```
AVAILABLE WORKFLOWS (from the system registry):
  - research_intelligence: Gathers news and expert analysis on a topic
  - content_pipeline: Creates video scripts and assets from a research brief
  - social_publishing: Publishes content to TikTok, Instagram, and Facebook
  - lead_gen: Finds and scores business leads from maps and directories
  - supply_chain: Assesses supplier risk using location and audit data
```

**This list comes directly from your YAML files.**  
When you add a new workflow YAML, it appears in this list automatically.  
The LLM reads it and can now include your new workflow in plans.

---

## What happens when the planner returns a plan

`planner_node` produces an `ExecutionPlan` object (in `memory/schemas.py`):

```json
{
  "complexity": "multi_workflow",
  "workflow_plan": ["research_intelligence", "content_pipeline", "social_publishing"],
  "department_sequence": ["research", "content", "distribution"],
  "reasoning": "Research summary feeds into script writing; video URL feeds into publishing.",
  "estimated_steps": 12
}
```

The node then writes these fields into `AgentState`:

```python
return {
    "workflow_plan": plan.workflow_plan,
    "current_workflow_index": 0,       # already 0 from make_initial_state
    "department": plan.department_sequence[0],
    "goal": intent_output["goal"],
}
```

From here, `workflow_progression_router` takes over and routes to the first subgraph.

---

## What makes the planner wrong (and how to fix it)

The planner makes mistakes when:

| Problem | Cause | Fix |
|---|---|---|
| Picks the wrong workflow | Vague `purpose` in YAML | Write a clearer `purpose` sentence |
| Adds unnecessary workflows | Ambiguous user query | Add example queries to the workflow YAML |
| Uses `multi_workflow` when `direct` was enough | Missing "direct" examples in prompt | The prompt already has these — or add more examples |
| Picks the right workflow but wrong order | Dependencies not described | Mention dependencies in `purpose` field |

**The most impactful fix is always: write a better `purpose` in your YAML.**

---

## How to influence the planner without touching code

### Method 1: Improve your `purpose` descriptions

Vague (planner confused):
```yaml
purpose: >
  Does research stuff.
```

Clear (planner decides correctly):
```yaml
purpose: >
  Searches the web, social media, and Reddit for recent news on the user's topic,
  then summarizes the findings into a structured brief that content agents can use
  as the basis for scripts and articles.
```

### Method 2: Add `example_queries` to workflow YAML

```yaml
example_queries:
  - "Research what's trending in sustainable fashion this week"
  - "Tell me the latest news about AI regulation in Europe"
  - "What are people saying about electric vehicles on social media today?"
```

The planner prompt includes these as hints for when to use this workflow.

### Method 3: Add a `do_not_use_if` field

```yaml
do_not_use_if: >
  The user only wants a quick factual answer without a full research report.
  Use "direct_answer" instead for simple questions.
```

This helps the planner avoid over-engineering simple requests.

---

## The `ExecutionPlan` object (memory/schemas.py)

```python
class ExecutionPlan(BaseModel):
    complexity: str           # "direct" | "single_workflow" | "multi_workflow"
    workflow_plan: list[str]  # e.g. ["research_intelligence", "content_pipeline"]
    department_sequence: list[str]  # e.g. ["research", "content"]
    reasoning: str            # why this plan was chosen (shown to user/logs)
    estimated_steps: int      # rough agent count (used for UI progress bar)
```

This object is validated by Pydantic before the planner writes to state.
If the LLM returns malformed JSON, Pydantic catches it and the planner retries.

---

## Summary

```
planner intelligence
    = LLM reasoning
    + your YAML purpose descriptions      ← you control this
    + your example_queries in YAML        ← you control this
    + the prompt template                 ← developer controls this
```

You don't need to be a developer to improve the planner.
Write better `purpose` and `example_queries` fields in your YAML files.
That is the most direct way to make the planner smarter.
