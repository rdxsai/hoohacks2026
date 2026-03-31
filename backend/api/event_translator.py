"""
Translate LangGraph astream_events into frontend PipelineEvent format.

Uses a stateful translator that tracks which top-level node is active
to correctly attribute tool calls and LLM events to their agent.
"""

from __future__ import annotations

import time
from typing import Any


# Top-level pipeline nodes → agent names
_TOP_NODES = {
    "classifier_node": "classifier",
    "analyst_node": "analyst",
    "premium_node": "lightning",
    "housing_node": "Housing",
    "consumer_node": "Consumer",
    "sector_barrier": None,
    "synthesis_node": "synthesis",
}

# Sub-graph phase nodes → agent + phase number
# Nodes with unique names get a direct lookup.
# Nodes with shared names (phase_2_baseline, phase_5_scorecard, phase_5_minimal)
# are resolved at runtime using the active agent context.
_PHASE_NODES: dict[str, tuple[str, str, str]] = {}  # name → (agent, phase_num, label)

# Analyst phases (all unique names)
_ANALYST_PHASE_NODES = {
    "phase_1_policy_spec": ("analyst", "1", "Policy Specification"),
    "phase_2_baseline": ("analyst", "2", "Baseline & Counterfactual"),
    "phase_3_transmission": ("analyst", "3", "Transmission Mapping"),
    "phase_4_evidence": ("analyst", "4", "Evidence Gathering"),
    "phase_5_synthesis": ("analyst", "5", "Synthesis & Briefing"),
}

# Housing phases
_HOUSING_PHASE_NODES = {
    "phase_1_pathways": ("Housing", "1", "Identifying transmission pathways"),
    "housing_phase_1_pathways": ("Housing", "1", "Identifying transmission pathways"),
    "phase_2_baseline": ("Housing", "2", "Gathering housing market baseline"),
    "phase_3_magnitudes": ("Housing", "3", "Estimating impact magnitudes"),
    "phase_4_distributional": ("Housing", "4", "Distributional & temporal analysis"),
    "phase_5_scorecard": ("Housing", "5", "Building affordability scorecard"),
    "phase_5_minimal": ("Housing", "5", "Minimal impact assessment"),
}

# Consumer phases
_CONSUMER_PHASE_NODES = {
    "phase_1_shock": ("Consumer", "1", "Identifying price shock entry points"),
    "consumer_phase_1_shock": ("Consumer", "1", "Identifying price shock entry points"),
    "phase_2_passthrough": ("Consumer", "2", "Estimating pass-through rates"),
    "phase_3_geo_behavioral": ("Consumer", "3", "Geographic & behavioral analysis"),
    "phase_4_purchasing_power": ("Consumer", "4", "Net purchasing power calculation"),
    "phase_5_scorecard": ("Consumer", "5", "Building consumer impact scorecard"),
    "phase_5_minimal": ("Consumer", "5", "Minimal impact assessment"),
}

# Synthesis phases (all unique names)
_SYNTHESIS_PHASE_NODES = {
    "phase_1_audit": ("synthesis", "1", "Consistency Audit"),
    "phase_2_impact": ("synthesis", "2", "Net Household Impact"),
    "phase_3_winners": ("synthesis", "3", "Winners & Losers"),
    "phase_4_narrative": ("synthesis", "4", "Narrative & Timeline"),
    "phase_5_payload": ("synthesis", "5", "Analytics Payload"),
}

# Unique names go in the global lookup
for d in [_ANALYST_PHASE_NODES, _HOUSING_PHASE_NODES, _CONSUMER_PHASE_NODES, _SYNTHESIS_PHASE_NODES]:
    for k, v in d.items():
        if k not in _PHASE_NODES:
            _PHASE_NODES[k] = v
        # For collisions, we rely on active_agent context (see _resolve_phase)


class EventTranslator:
    """Stateful translator — tracks active agent to attribute tool calls."""

    # Phase node lookups per agent — used for disambiguation
    _AGENT_PHASES = {
        "analyst": _ANALYST_PHASE_NODES,
        "Housing": _HOUSING_PHASE_NODES,
        "Consumer": _CONSUMER_PHASE_NODES,
        "synthesis": _SYNTHESIS_PHASE_NODES,
    }

    def __init__(self) -> None:
        self._active_agent: str | None = None
        self._active_phase: str = "0"

    def _resolve_phase(self, name: str) -> tuple[str, str, str] | None:
        """Resolve a phase node name, using active agent to disambiguate collisions."""
        # Try active agent's lookup first (handles shared names like phase_2_baseline)
        if self._active_agent and self._active_agent in self._AGENT_PHASES:
            agent_lookup = self._AGENT_PHASES[self._active_agent]
            if name in agent_lookup:
                return agent_lookup[name]
        # Fall back to global unique lookup
        return _PHASE_NODES.get(name)

    def translate(self, event: dict[str, Any]) -> dict[str, Any] | None:
        kind = event.get("event", "")
        name = event.get("name", "")
        data = event.get("data", {})
        ts = time.time()

        # ==============================================================
        # Custom events (from adispatch_custom_event in node functions)
        # ==============================================================
        if kind == "on_custom_event":
            return self._handle_custom(name, data, ts)

        # ==============================================================
        # Top-level node lifecycle
        # ==============================================================
        if kind == "on_chain_start" and name in _TOP_NODES:
            agent = _TOP_NODES[name]
            if agent:
                self._active_agent = agent
            return None  # Custom events handle agent_start

        if kind == "on_chain_end" and name in _TOP_NODES:
            return None  # Custom events handle agent_complete

        # ==============================================================
        # Sub-graph phase transitions
        # ==============================================================
        if kind == "on_chain_start":
            resolved = self._resolve_phase(name)
            if resolved:
                agent, num, label = resolved
                self._active_agent = agent
                self._active_phase = num
                return self._phase_event(agent, num, label, "start", ts)

        if kind == "on_chain_end":
            resolved = self._resolve_phase(name)
            if resolved:
                agent, num, label = resolved
                return self._phase_event(agent, num, label, "complete", ts)

        # ==============================================================
        # Tool calls — attributed to the currently active agent
        # ==============================================================
        if kind == "on_tool_start":
            return self._tool_event(name, data, ts)

        if kind == "on_tool_end":
            # Emit tool results for richer frontend display
            tool_output = data.get("output", "")
            output_str = str(tool_output)[:120] if tool_output else ""
            agent = self._active_agent or "analyst"
            if agent == "analyst":
                return {"type": "analyst_thinking", "agent": "analyst", "data": {
                    "step_type": "tool_result", "content": f"{name} → {output_str}",
                    "phase": self._active_phase, "tool": name,
                }, "timestamp": ts}
            if agent in ("Housing", "Consumer"):
                return {"type": "sector_agent_thinking", "agent": agent, "data": {
                    "agent": agent, "step_type": "tool_result",
                    "content": f"{name} → {output_str}",
                    "phase": self._active_phase, "tool": name,
                }, "timestamp": ts}
            return None

        # ==============================================================
        # LLM activity
        # ==============================================================
        if kind == "on_chat_model_start":
            agent = self._active_agent
            if agent == "analyst":
                return {"type": "analyst_thinking", "agent": "analyst", "data": {
                    "step_type": "reasoning", "content": "Analyzing data...",
                    "phase": self._active_phase,
                }, "timestamp": ts}
            if agent in ("Housing", "Consumer"):
                return {"type": "sector_agent_thinking", "agent": agent, "data": {
                    "agent": agent, "step_type": "reasoning",
                    "content": "Analyzing data and forming conclusions...",
                    "phase": self._active_phase,
                }, "timestamp": ts}
            if agent == "synthesis":
                return {"type": "synthesis_thinking", "agent": "synthesis", "data": {
                    "step_type": "reasoning", "content": "Analyzing...",
                    "phase": self._active_phase,
                }, "timestamp": ts}
            return None

        return None

    def _handle_custom(self, name: str, data: Any, ts: float) -> dict | None:
        if not isinstance(data, dict):
            data = {}

        mapping = {
            "classifier_start": ("agent_start", "classifier"),
            "classifier_thinking": ("classifier_thinking", "classifier"),
            "classifier_complete": ("classifier_complete", "classifier"),
            "agent_start": ("agent_start", data.get("agent", "")),
            "analyst_complete": ("analyst_complete", "analyst"),
            "analyst_thinking": ("analyst_thinking", "analyst"),
            "lightning_payment": ("lightning_payment", "lightning"),
            "sector_agent_started": ("sector_agent_started", data.get("agent", data.get("sector", ""))),
            "sector_agent_complete": ("sector_agent_complete", data.get("agent", data.get("sector", ""))),
            "synthesis_complete": ("synthesis_complete", "synthesis"),
        }

        if name in mapping:
            event_type, agent = mapping[name]
            return {"type": event_type, "agent": agent, "data": data, "timestamp": ts}

        # Tool activity from tool wrappers (via _record_timing_async)
        if name == "tool_activity":
            tool = data.get("tool", "")
            args = data.get("args", "")
            duration = data.get("duration_ms", 0)
            success = data.get("success", True)
            status = "OK" if success else "FAIL"
            agent = self._active_agent or "analyst"

            if agent == "analyst":
                return {"type": "analyst_thinking", "agent": "analyst", "data": {
                    "step_type": "tool_call" if success else "tool_result",
                    "content": f"{tool}({args}) → {duration}ms {status}",
                    "phase": self._active_phase, "tool": tool,
                }, "timestamp": ts}
            if agent in ("Housing", "Consumer"):
                return {"type": "sector_agent_thinking", "agent": agent, "data": {
                    "agent": agent, "step_type": "tool_call",
                    "content": f"{tool}({args}) → {duration}ms {status}",
                    "phase": self._active_phase, "tool": tool,
                }, "timestamp": ts}
            if agent == "synthesis":
                return {"type": "synthesis_thinking", "agent": "synthesis", "data": {
                    "step_type": "tool_call",
                    "content": f"{tool}({args}) → {duration}ms {status}",
                    "phase": self._active_phase, "tool": tool,
                }, "timestamp": ts}
            return None

        # Tool call/result forwarded from _run_subgraph
        if name.endswith("_tool_call"):
            agent = data.get("agent", name.replace("_tool_call", ""))
            tool = data.get("tool", "")
            args = data.get("args", "")
            if agent == "analyst":
                return {"type": "analyst_thinking", "agent": "analyst", "data": {
                    "step_type": "tool_call", "content": f"{tool}({args})",
                    "phase": self._active_phase, "tool": tool,
                }, "timestamp": ts}
            if agent in ("Housing", "Consumer"):
                return {"type": "sector_agent_thinking", "agent": agent, "data": {
                    "agent": agent, "step_type": "tool_call",
                    "content": f"Calling {tool}({args})",
                    "phase": self._active_phase, "tool": tool,
                }, "timestamp": ts}
            if agent == "synthesis":
                return {"type": "synthesis_thinking", "agent": "synthesis", "data": {
                    "step_type": "tool_call", "content": f"Calling {tool}({args})",
                    "phase": self._active_phase, "tool": tool,
                }, "timestamp": ts}

        if name.endswith("_tool_result"):
            agent = data.get("agent", name.replace("_tool_result", ""))
            tool = data.get("tool", "")
            result_str = data.get("result", "")
            if agent == "analyst":
                return {"type": "analyst_thinking", "agent": "analyst", "data": {
                    "step_type": "tool_result", "content": f"{tool} → {result_str}",
                    "phase": self._active_phase, "tool": tool,
                }, "timestamp": ts}
            if agent in ("Housing", "Consumer"):
                return {"type": "sector_agent_thinking", "agent": agent, "data": {
                    "agent": agent, "step_type": "tool_result",
                    "content": f"{tool} → {result_str}",
                    "phase": self._active_phase, "tool": tool,
                }, "timestamp": ts}
            return None

        # Pass through unknown custom events
        return {"type": name, "data": data, "timestamp": ts}

    def _phase_event(self, agent: str, num: str, label: str, status: str, ts: float) -> dict:
        if agent == "analyst":
            step = "phase_start" if status == "start" else "phase_complete"
            content = f"Phase {num}: {label}" if status == "start" else f"Phase {num} ({label}) complete"
            return {"type": "analyst_thinking", "agent": "analyst", "data": {
                "step_type": step, "content": content, "phase": num,
            }, "timestamp": ts}

        if agent in ("Housing", "Consumer"):
            if status == "start":
                return {"type": "sector_agent_tool_call", "agent": agent, "data": {
                    "agent": agent, "tool": f"phase_{num}", "query": label,
                    "phase": num, "phase_detail": label,
                }, "timestamp": ts}
            else:
                return {"type": "sector_agent_thinking", "agent": agent, "data": {
                    "agent": agent, "step_type": "phase_complete",
                    "content": f"Phase {num} complete: {label}", "phase": num,
                }, "timestamp": ts}

        if agent == "synthesis":
            if status == "start":
                return {"type": "synthesis_thinking", "agent": "synthesis", "data": {
                    "step_type": "phase_start", "content": f"Phase {num}: {label}", "phase": num,
                }, "timestamp": ts}
            else:
                return {"type": "synthesis_phase", "agent": "synthesis", "data": {
                    "phase": int(num), "name": label, "status": "complete",
                }, "timestamp": ts}

        return {"type": "agent_thinking", "data": {"agent": agent, "phase": num, "label": label}, "timestamp": ts}

    def _tool_event(self, tool_name: str, data: dict, ts: float) -> dict | None:
        tool_input = data.get("input", {})
        if isinstance(tool_input, dict):
            args_str = ", ".join(f"{k}={v}" for k, v in list(tool_input.items())[:3])
        else:
            args_str = str(tool_input)[:100]

        agent = self._active_agent or "analyst"

        if agent == "analyst":
            return {"type": "analyst_thinking", "agent": "analyst", "data": {
                "step_type": "tool_call", "content": f"{tool_name}({args_str[:80]})",
                "phase": self._active_phase, "tool": tool_name,
            }, "timestamp": ts}

        if agent in ("Housing", "Consumer"):
            return {"type": "sector_agent_thinking", "agent": agent, "data": {
                "agent": agent, "step_type": "tool_call",
                "content": f"Calling {tool_name}({args_str[:80]})",
                "phase": self._active_phase, "tool": tool_name,
            }, "timestamp": ts}

        if agent == "synthesis":
            return {"type": "synthesis_thinking", "agent": "synthesis", "data": {
                "step_type": "tool_call", "content": f"Calling {tool_name}({args_str[:80]})",
                "phase": self._active_phase, "tool": tool_name,
            }, "timestamp": ts}

        return {"type": "analyst_tool_call", "agent": "analyst", "data": {
            "tool": tool_name, "query": args_str,
        }, "timestamp": ts}
