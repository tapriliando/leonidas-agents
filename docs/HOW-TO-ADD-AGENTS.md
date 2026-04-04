# How to add a new agent, workflow, or tool

**You do not need to write Python to add new agents or workflows.**  
Everything the system needs to know is declared in YAML files.  
A developer only gets involved when you need to connect a real external API.

---

## The three levels of work

| What you want to add | Who does it | Files touched |
|---|---|---|
| A new agent to an existing workflow | You (non-technical) | 1 YAML file |
| A new workflow using existing tools | You + 1 developer task | 1 YAML file + 1 graph file |
| A new external tool (e.g. new API) | Developer | 1 YAML + 1 Python handler |

---

## Level 1 — Adding a new agent (YAML only)

An agent is one step in a workflow. It does one job.

**Example:** You want to add a "LinkedIn scraper agent" to the research workflow.

**Step 1:** Open this folder in your file explorer:
```
docs/registry/agents/
```

**Step 2:** Copy `_template.yaml` and rename it:
```
linkedin_scraper_node.yaml
```

**Step 3:** Fill in the fields — here is a completed example:

```yaml
agent_id: linkedin_scraper_node
name: "LinkedIn Scraper Agent"
version: "1.0.0"
department: research
workflow_types:
  - research_intelligence

purpose: >
  Searches LinkedIn for recent posts and company updates
  relevant to the user's query topic.

state_reads:
  - user_query
  - constraints

state_writes:
  - artifacts

workflow_data_writes:
  - key: linkedin_posts
    type: list
    description: Recent LinkedIn posts matching the query topic

tools:
  - mcp.web_search

routing_keys:
  success: merge
  failure: retry
```

**Step 4:** Save the file. That's it.

The system picks it up automatically on the next run. The planner will
see your new agent and include it when building the research workflow plan.

---

## Level 2 — Adding a new workflow (YAML + one developer task)

A workflow is a sequence of agents for a specific type of job.

**Example:** You want a "HR recruitment pipeline" workflow.

**Your part (YAML):**

Copy `docs/registry/workflows/_template.yaml` and save as
`docs/registry/workflows/hr_recruitment.yaml`:

```yaml
workflow_type: hr_recruitment
name: "HR Recruitment Pipeline"
version: "1.0.0"
department: operations

purpose: >
  Screens job applications, scores candidates against the role requirements,
  and drafts interview invitations for shortlisted candidates.

nodes:
  - name: intent_node
    writes: [goal, workflow_type, department, constraints]
  - name: fetch_applications_node
    writes: [artifacts.workflow_data.applications]
  - name: score_candidates_node
    writes: [artifacts.workflow_data.scores]
  - name: draft_invitations_node
    writes: [artifacts.workflow_data.invitations]
  - name: persist_node
    writes: [status]

output_schema:
  workflow_data:
    applications: list of candidate profiles
    scores: score per candidate
    invitations: drafted interview emails

example_queries:
  - "Screen the 20 latest applications for the senior engineer role"
  - "Find and score candidates who applied this week for product manager"
```

**Developer task (one graph file):**

The developer creates `backend/app/graph/workflows/hr_recruitment.py`
to wire the node sequence. They copy an existing workflow graph and adjust
the node names. This is the only code change.

---

## Level 3 — Adding a new external tool (developer task)

A tool is an external service (API) the agents can call.

**Your part (YAML):**

Copy `docs/registry/tools/_template.yaml` and save as
`docs/registry/tools/mcp.linkedin_search.yaml`:

```yaml
tool_id: mcp.linkedin_search
name: "LinkedIn Search"
version: "1.0.0"
provider: rapidapi

purpose: >
  Searches LinkedIn posts and profiles by keyword using the RapidAPI LinkedIn endpoint.

input_schema:
  required:
    - query: string
  optional:
    - max_results: number

output_schema:
  - posts: list of post objects

auth:
  method: api_key
  env_var: RAPIDAPI_KEY

used_by_agents:
  - linkedin_scraper_node
```

**Developer task:**

They add the Python handler in `mcp-server/tools/linkedin_search.py` and
add one line to `mcp-server/registry.yaml`. No other code changes.

---

## How the planner stays aware of everything

The planner agent reads the registry at runtime. When you add a new YAML file,
the planner sees it immediately on the next run — nothing is hard-coded.

Here is what the planner receives when building a plan for `research_intelligence`:

```
Available agents for this workflow:
  - web_crawler_node: Crawls top web search results for the query context
  - social_media_scraper_node: Scrapes recent posts from Twitter/X and LinkedIn
  - reddit_scraper_node: Fetches top Reddit threads matching the query
  - linkedin_scraper_node: Searches LinkedIn posts and company updates    ← your new one
  - summarizer_node: Condenses all raw sources into a structured brief
  - business_analyst_node: Analyses business impact of the research findings
  - report_node: Compiles the final report
```

The planner uses this list to decide which agents to include in its execution plan.

---

## Quick reference — what each YAML field means

| Field | What to write |
|---|---|
| `agent_id` | Unique name in `snake_case`. Match the Python filename (developer fills that). |
| `name` | Human-readable label, e.g. `"LinkedIn Scraper Agent"` |
| `department` | Which team owns this: `analytics`, `content`, `distribution`, `research`, `operations` |
| `workflow_types` | List of workflow names this agent can participate in. Use `["*"]` for shared agents. |
| `purpose` | One sentence. What does this agent do? |
| `state_reads` | Which state fields does it need? Usually `user_query`, `constraints`, `artifacts`. |
| `state_writes` | Which state fields does it update? Usually `artifacts` and/or `metrics`. |
| `workflow_data_writes` | The specific key it adds inside `artifacts.workflow_data`. |
| `tools` | List of `mcp.<tool_id>` strings. Check `docs/registry/tools/` for available tools. |
| `routing_keys.success` | Label returned when the agent succeeds (tells the graph what to do next). |
| `routing_keys.failure` | Label returned when the agent fails. |

---

## What a developer does after you add a YAML

When you hand a YAML to a developer, they do exactly **two things**:

1. Create `backend/app/agents/<department>/<agent_id>.py`  
   (the Python function that implements the agent — reads state, calls MCP, returns dict)

2. Wire the node into the workflow graph in `backend/app/graph/workflows/<workflow>.py`  
   (two lines: `add_node(...)` and `add_edge(...)`)

Nothing else changes. No database migrations, no schema changes, no config restarts.

---

## File locations at a glance

```
docs/
└── registry/
    ├── agents/
    │   ├── _template.yaml        ← copy this
    │   └── <your_agent>.yaml     ← your new file goes here
    ├── tools/
    │   ├── _template.yaml        ← copy this
    │   └── <your_tool>.yaml
    ├── workflows/
    │   ├── _template.yaml        ← copy this
    │   └── <your_workflow>.yaml
    └── skills/
        └── _template.yaml
```
