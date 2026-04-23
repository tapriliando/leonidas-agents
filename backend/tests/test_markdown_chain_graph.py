"""Declarative markdown_chain subgraph compiles."""

from __future__ import annotations

from app.graph.workflows.markdown_chain_graph import build_markdown_chain_graph


def test_build_markdown_chain_graph_compiles():
    g = build_markdown_chain_graph()
    assert g is not None
    nodes = list(getattr(g, "nodes", {}))
    assert any(n.startswith("md_") for n in nodes)
    assert "persist" in nodes
