"""
report_node.py — Formats the final human-readable markdown report for any workflow.

NODE CONTRACT:
  Reads:  workflow_type, goal, metrics, artifacts.workflow_data
  Writes: artifacts.report  (markdown string)
  Calls:  nothing — pure computation, no LLM, no MCP

DESIGN:
  This node is completely generic. It iterates over the standardized workflow_data keys
  (items, summary, suggestions, scored_items, item_scores, analytics) and renders
  each section only when the key is present.

  workflow_type and goal appear only in the report header as context strings.
  The node NEVER uses `if workflow_type == "complaint_analysis":` dispatch — any new
  workflow that writes to the standard keys gets a correctly structured report with
  zero changes here.

  Standardized key → report section mapping:
    "items"         → "Source Data"      (row count, sample)
    "summary"       → "Summary"          (overview + key findings)
    "suggestions"   → "Recommendations"  (action list with priorities)
    "enriched_items"→ "Enriched Data"    (count + enrichment coverage)
    "scored_items"  → "Scored Items"     (top items preview)
    "item_scores"   → included inside Scored Items section
    "analytics"          → "Analytics"           (score distribution, top items)
    "content_generation" → "Generated content"   (HeyGen / other providers)
    "content_prompt"     → optional inline hint in fallback (stored brief for video)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.state import AgentState


def report_node(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: builds the final markdown report from workflow_data.

    Synchronous — no I/O. Returns only the artifacts field (with report set).
    """
    workflow_type: str = state.get("workflow_type") or "unknown"
    goal: str = state.get("goal") or "workflow run"
    metrics = state.get("metrics") or {}
    artifacts = state.get("artifacts") or {}
    workflow_data: dict = artifacts.get("workflow_data") or {}

    sections: list[str] = []

    # ── Header ───────────────────────────────────────────────────────────────
    sections.append(f"# Workflow Report: {goal.replace('_', ' ').title()}")
    sections.append(f"**Workflow type:** `{workflow_type}`")

    item_count = metrics.get("item_count")
    quality_score = metrics.get("quality_score")
    confidence = metrics.get("confidence")

    meta_parts = []
    if item_count is not None:
        meta_parts.append(f"Items processed: **{item_count}**")
    if quality_score is not None:
        meta_parts.append(f"Quality score: **{quality_score:.2f}**")
    if confidence is not None:
        meta_parts.append(f"Confidence: **{confidence:.2f}**")
    if meta_parts:
        sections.append(" · ".join(meta_parts))

    sections.append("")

    # ── Source data (items) ───────────────────────────────────────────────────
    items: list = workflow_data.get("items") or []
    if items:
        sections.append("## Source Data")
        sections.append(f"Retrieved **{len(items)}** items.")
        if items:
            sample = items[:3]
            for i, item in enumerate(sample, 1):
                name = item.get("name") or item.get("title") or item.get("id") or f"item_{i}"
                url = item.get("url") or ""
                line = f"- {name}"
                if url:
                    line = f"- [{name}]({url})"
                sections.append(line)
            if len(items) > 3:
                sections.append(f"- *(and {len(items) - 3} more)*")
        sections.append("")

    # ── Summary ──────────────────────────────────────────────────────────────
    summary: dict = workflow_data.get("summary") or {}
    if summary:
        sections.append("## Summary")
        overview = summary.get("overview") or summary.get("text") or ""
        if overview:
            sections.append(overview)
        findings: list = summary.get("key_findings") or []
        if findings:
            sections.append("")
            sections.append("**Key findings:**")
            for finding in findings:
                sections.append(f"- {finding}")
        sections.append("")

    # ── Recommendations (suggestions) ────────────────────────────────────────
    suggestions: list = workflow_data.get("suggestions") or []
    if suggestions:
        sections.append("## Recommendations")
        for item in suggestions:
            action = item.get("action") or str(item)
            priority = item.get("priority") or "medium"
            rationale = item.get("rationale") or ""
            priority_badge = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
                priority.lower(), "•"
            )
            line = f"{priority_badge} **{action}**"
            if rationale:
                line += f" — {rationale}"
            sections.append(line)
        sections.append("")

    # ── Enriched items ────────────────────────────────────────────────────────
    enriched: list = workflow_data.get("enriched_items") or []
    if enriched:
        covered = sum(1 for e in enriched if e.get("context"))
        sections.append("## Enriched Data")
        sections.append(
            f"Enriched **{len(enriched)}** items; "
            f"**{covered}** have additional context ({int(covered / len(enriched) * 100)}% coverage)."
        )
        sections.append("")

    # ── Scored items ──────────────────────────────────────────────────────────
    scored: list = workflow_data.get("scored_items") or []
    item_scores: dict = workflow_data.get("item_scores") or {}
    if scored:
        sections.append("## Scored Items")
        sections.append(f"Scored **{len(scored)}** items.")
        # Show top 5 by score
        scored_with_score = [
            (item, item_scores.get(str(item.get("id", "")), item.get("score", 0.0)))
            for item in scored
        ]
        scored_with_score.sort(key=lambda x: x[1], reverse=True)
        sections.append("")
        sections.append("**Top items:**")
        for item, score in scored_with_score[:5]:
            name = item.get("name") or item.get("title") or item.get("id") or "unknown"
            priority = item.get("priority") or ""
            priority_str = f" ({priority})" if priority else ""
            sections.append(f"- {name}{priority_str} — score: `{score:.2f}`")
        sections.append("")

    # ── Analytics ─────────────────────────────────────────────────────────────
    analytics: dict = workflow_data.get("analytics") or {}
    if analytics:
        sections.append("## Analytics")
        avg = analytics.get("avg_score")
        if avg is not None:
            sections.append(f"Average score: **{avg:.2f}**")
        dist: dict = analytics.get("score_distribution") or {}
        if dist:
            sections.append("")
            sections.append("**Score distribution:**")
            for bucket, count in sorted(dist.items()):
                bar = "█" * min(count, 20)
                sections.append(f"- `{bucket}`: {bar} ({count})")
        priority_counts: dict = analytics.get("priority_counts") or {}
        if priority_counts:
            sections.append("")
            sections.append("**Priority breakdown:**")
            for prio, count in sorted(priority_counts.items()):
                sections.append(f"- {prio}: {count}")
        sections.append("")

    # ── Generated content (HeyGen, etc.) ─────────────────────────────────────
    content_gen: dict = workflow_data.get("content_generation") or {}
    if content_gen:
        sections.append("## Generated content")
        prov = content_gen.get("provider") or "unknown"
        sections.append(f"**Provider:** `{prov}`")
        pu = content_gen.get("prompt_used")
        if pu:
            sections.append("")
            sections.append("**Prompt sent to API:**")
            for line in str(pu).splitlines() or [str(pu)]:
                sections.append(f"> {line}")
        resp = content_gen.get("response")
        if resp is not None:
            sections.append("")
            sections.append("**API response:**")
            try:
                pretty = json.dumps(resp, ensure_ascii=False, indent=2, default=str)
            except (TypeError, ValueError):
                pretty = str(resp)
            plines = pretty.splitlines()
            sections.append("```json")
            for pl in plines[:80]:
                sections.append(pl)
            if len(plines) > 80:
                sections.append("… *(truncated)*")
            sections.append("```")
        sections.append("")

    # ── Fallback for empty workflow_data ──────────────────────────────────────
    has_any = any(
        [
            items,
            summary,
            suggestions,
            enriched,
            scored,
            analytics,
            content_gen,
            workflow_data.get("content_prompt"),
        ]
    )
    if not has_any:
        sections.append("*No output data was produced by this workflow run.*")

    report = "\n".join(sections)

    updated_artifacts = {**artifacts, "report": report}
    return {"artifacts": updated_artifacts}
