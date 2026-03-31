"""
Translate LangGraph astream_events into frontend PipelineEvent format.

LangGraph emits structured events: on_chain_start, on_chain_end,
on_tool_start, on_tool_end, on_chat_model_start, on_chat_model_stream,
on_custom_event. This module maps them to the event types the frontend
expects (classifier_complete, analyst_tool_call, sector_agent_started, etc.)
so zero frontend changes are needed.
"""

from __future__ import annotations

import time
from typing import Any


# Maps sub-graph node names to the agent that owns them
_ANALYST_PHASES = {
    "phase_1_policy_spec", "phase_2_baseline", "phase_3_transmission",
    "phase_4_evidence", "phase_5_synthesis",
}
_HOUSING_PHASES = {
    "phase_1_pathways", "phase_2_baseline", "phase_3_magnitudes",
    "phase_4_distributional", "phase_5_scorecard", "phase_5_minimal",
}
_CONSUMER_PHASES = {
    "phase_1_shock", "phase_2_passthrough", "phase_3_geo_behavioral",
    "phase_4_purchasing_power", "phase_5_scorecard", "phase_5_minimal",
}
_SYNTHESIS_PHASES = {
    "phase_1_audit", "phase_2_impact", "phase_3_winners",
    "phase_4_narrative", "phase_5_payload",
}

_PHASE_LABELS = {
    # Analyst
    "phase_1_policy_spec": "Policy Specification",
    "phase_2_baseline": "Baseline & Counterfactual",
    "phase_3_transmission": "Transmission Mapping",
    "phase_4_evidence": "Evidence Gathering",
    "phase_5_synthesis": "Synthesis & Briefing",
    # Housing
    "phase_1_pathways": "Identifying transmission pathways",
    "phase_2_baseline": "Gathering housing market baseline",
    "phase_3_magnitudes": "Estimating impact magnitudes",
    "phase_4_distributional": "Distributional & temporal analysis",
    "phase_5_scorecard": "Building affordability scorecard",
    "phase_5_minimal": "Minimal impact assessment",
    # Consumer
    "phase_1_shock": "Identifying price shock entry points",
    "phase_2_passthrough": "Estimating pass-through rates",
    "phase_3_geo_behavioral": "Geographic & behavioral analysis",
    "phase_4_purchasing_power": "Net purchasing power calculation",
    # Synthesis
    "phase_1_audit": "Consistency Audit",
    "phase_2_impact": "Net Household Impact",
    "phase_3_winners": "Winners & Losers",
    "phase_4_narrative": "Narrative & Timeline",
    "phase_5_payload": "Analytics Payload",
}


def _infer_agent(name: str) -> str | None:
    """Determine which agent a sub-graph node belongs to."""
    if name in _ANALYST_PHASES:
        return "analyst"
    if name in _HOUSING_PHASES:
        return "Housing"
    if name in _CONSUMER_PHASES:
        return "Consumer"
    if name in _SYNTHESIS_PHASES:
        return "synthesis"
    return None


def _phase_number(name: str) -> str:
    """Extract phase number from node name like 'phase_3_magnitudes' → '3'."""
    parts = name.split("_")
    if len(parts) >= 2 and parts[1].isdigit():
        return parts[1]
    return "0"


def translate_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Map a LangGraph astream_events event to a frontend PipelineEvent.

    Returns None for events that should be filtered out (internal plumbing).
    """
    kind = event.get("event", "")
    name = event.get("name", "")
    data = event.get("data", {})
    ts = time.time()

    # ------------------------------------------------------------------
    # Custom events (from adispatch_custom_event in node functions)
    # These are already in the frontend's expected format.
    # ------------------------------------------------------------------
    if kind == "on_custom_event":
        event_name = name  # e.g. "classifier_complete", "sector_agent_started"
        event_data = data if isinstance(data, dict) else {}

        # Map custom event names to frontend event types
        if event_name == "classifier_start":
            return {"type": "agent_start", "agent": "classifier", "data": event_data, "timestamp": ts}
        if event_name == "classifier_thinking":
            return {"type": "classifier_thinking", "agent": "classifier", "data": event_data, "timestamp": ts}
        if event_name == "classifier_complete":
            return {"type": "classifier_complete", "agent": "classifier", "data": event_data, "timestamp": ts}
        if event_name == "agent_start":
            return {"type": "agent_start", "agent": event_data.get("agent", ""), "data": event_data, "timestamp": ts}
        if event_name == "analyst_complete":
            return {"type": "analyst_complete", "agent": "analyst", "data": event_data, "timestamp": ts}
        if event_name == "analyst_thinking":
            return {"type": "analyst_thinking", "agent": "analyst", "data": event_data, "timestamp": ts}
        if event_name == "lightning_payment":
            return {"type": "lightning_payment", "data": event_data, "timestamp": ts}
        if event_name == "sector_agent_started":
            agent = event_data.get("agent", event_data.get("sector", ""))
            return {"type": "sector_agent_started", "agent": agent, "data": event_data, "timestamp": ts}
        if event_name == "sector_agent_complete":
            agent = event_data.get("agent", event_data.get("sector", ""))
            return {"type": "sector_agent_complete", "agent": agent, "data": event_data, "timestamp": ts}
        if event_name == "synthesis_complete":
            return {"type": "synthesis_complete", "agent": "synthesis", "data": event_data, "timestamp": ts}

        # Pass through any other custom events as-is
        return {"type": event_name, "data": event_data, "timestamp": ts}

    # ------------------------------------------------------------------
    # Sub-graph phase transitions (on_chain_start / on_chain_end)
    # ------------------------------------------------------------------
    if kind == "on_chain_start":
        agent = _infer_agent(name)
        if agent:
            phase = _phase_number(name)
            label = _PHASE_LABELS.get(name, name)

            if agent == "analyst":
                return {"type": "analyst_thinking", "agent": "analyst", "data": {
                    "step_type": "phase_start", "content": f"Phase {phase}: {label}", "phase": phase,
                }, "timestamp": ts}

            if agent in ("Housing", "Consumer"):
                return {"type": "sector_agent_tool_call", "agent": agent, "data": {
                    "agent": agent, "tool": f"phase_{phase}", "query": label,
                    "phase": phase, "phase_detail": label,
                }, "timestamp": ts}

            if agent == "synthesis":
                return {"type": "synthesis_thinking", "agent": "synthesis", "data": {
                    "step_type": "phase_start", "content": f"Phase {phase}: {label}", "phase": phase,
                }, "timestamp": ts}

        return None

    if kind == "on_chain_end":
        agent = _infer_agent(name)
        if agent:
            phase = _phase_number(name)
            label = _PHASE_LABELS.get(name, name)

            if agent == "analyst":
                return {"type": "analyst_thinking", "agent": "analyst", "data": {
                    "step_type": "phase_complete", "content": f"Phase {phase} ({label}) complete", "phase": phase,
                }, "timestamp": ts}

            if agent in ("Housing", "Consumer"):
                return {"type": "sector_agent_thinking", "agent": agent, "data": {
                    "agent": agent, "step_type": "phase_complete",
                    "content": f"Phase {phase} complete: {label}", "phase": phase,
                }, "timestamp": ts}

            if agent == "synthesis":
                phase_data = {"phase": int(phase), "name": label, "status": "complete"}
                return {"type": "synthesis_phase", "agent": "synthesis", "data": phase_data, "timestamp": ts}

        return None

    # ------------------------------------------------------------------
    # Tool calls
    # ------------------------------------------------------------------
    if kind == "on_tool_start":
        tool_input = data.get("input", {})
        if isinstance(tool_input, dict):
            args_str = ", ".join(f"{k}={v}" for k, v in list(tool_input.items())[:3])
        else:
            args_str = str(tool_input)[:100]

        # Try to infer agent from parent tags
        tags = event.get("tags", [])
        agent = _agent_from_tags(tags)

        if agent == "analyst":
            return {"type": "analyst_tool_call", "agent": "analyst", "data": {
                "tool": name, "query": args_str,
            }, "timestamp": ts}

        if agent in ("Housing", "Consumer"):
            return {"type": "sector_agent_thinking", "agent": agent, "data": {
                "agent": agent, "step_type": "tool_call",
                "content": f"Calling {name}({args_str})", "tool": name,
            }, "timestamp": ts}

        if agent == "synthesis":
            return {"type": "synthesis_thinking", "agent": "synthesis", "data": {
                "step_type": "tool_call", "content": f"Calling {name}({args_str})", "tool": name,
            }, "timestamp": ts}

        # Generic tool call
        return {"type": "analyst_tool_call", "agent": "analyst", "data": {
            "tool": name, "query": args_str,
        }, "timestamp": ts}

    if kind == "on_tool_end":
        return None  # Tool results are noisy — skip

    # ------------------------------------------------------------------
    # LLM activity
    # ------------------------------------------------------------------
    if kind == "on_chat_model_start":
        tags = event.get("tags", [])
        agent = _agent_from_tags(tags)
        if agent in ("Housing", "Consumer"):
            return {"type": "sector_agent_thinking", "agent": agent, "data": {
                "agent": agent, "step_type": "reasoning",
                "content": "Analyzing data and forming conclusions...",
            }, "timestamp": ts}
        if agent == "synthesis":
            return {"type": "synthesis_thinking", "agent": "synthesis", "data": {
                "step_type": "reasoning", "content": "Analyzing...",
            }, "timestamp": ts}
        return None

    # Skip noisy events
    return None


def _agent_from_tags(tags: list[str]) -> str | None:
    """Infer agent from LangGraph event tags.

    LangGraph tags events with the graph hierarchy. We look for
    known node names in the tags to determine which agent emitted it.
    """
    tag_str = " ".join(tags).lower() if tags else ""
    if "housing" in tag_str:
        return "Housing"
    if "consumer" in tag_str:
        return "Consumer"
    if "synthesis" in tag_str:
        return "synthesis"
    if "analyst" in tag_str:
        return "analyst"
    return None
