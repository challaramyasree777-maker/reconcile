from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app import db
from app.services.categorization import categorize_transaction


class ReconcileState(TypedDict, total=False):
    transaction: dict[str, Any]
    memory: list[dict[str, Any]]
    decision: dict[str, Any]


def categorize_node(state: ReconcileState) -> ReconcileState:
    result = categorize_transaction(
        state["transaction"],
        state.get("memory", []),
    )
    return {"decision": result}


def evaluate_node(state: ReconcileState) -> ReconcileState:
    decision = dict(state["decision"])
    confidence = float(decision.get("confidence_score", 0))

    decision["action"] = "auto_post" if confidence >= 0.85 else "escalate"
    return {"decision": decision}


def route_after_evaluation(state: ReconcileState) -> str:
    return state["decision"]["action"]


def auto_post_node(state: ReconcileState) -> ReconcileState:
    from app.services.ledger import sync_pending_transactions

    transaction = state["transaction"]

    result = sync_pending_transactions()

    db.add_audit(
        transaction.get("id"),
        "auto_posted_to_ledger",
        {
            "decision": state["decision"],
            "ledger_result": result,
        },
    )

    return state


def escalate_node(state: ReconcileState) -> ReconcileState:
    transaction = state["transaction"]

    db.add_audit(
        transaction.get("id"),
        "human_review_required",
        state["decision"],
    )

    return state


def audit_node(state: ReconcileState) -> ReconcileState:
    transaction = state["transaction"]
    decision = state["decision"]

    db.add_audit(
        transaction.get("id"),
        "agent_decision",
        decision,
    )

    return state


def build_graph():
    graph = StateGraph(ReconcileState)

    graph.add_node("categorize", categorize_node)
    graph.add_node("evaluate", evaluate_node)
    graph.add_node("auto_post", auto_post_node)
    graph.add_node("escalate", escalate_node)
    graph.add_node("audit", audit_node)

    graph.add_edge(START, "categorize")
    graph.add_edge("categorize", "evaluate")

    graph.add_conditional_edges(
        "evaluate",
        route_after_evaluation,
        {
            "auto_post": "auto_post",
            "escalate": "escalate",
        },
    )

    graph.add_edge("auto_post", "audit")
    graph.add_edge("escalate", "audit")
    graph.add_edge("audit", END)

    return graph.compile()