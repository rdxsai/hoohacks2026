"""
Stage 2: Sector Agents — Housing and Consumer run as LangGraph multi-phase agents.

Each sector agent runs a 5-phase LangGraph StateGraph:
  Phase 1: Transmission pathway / price shock identification (reasoning)
  Phase 2: Baseline data gathering (ReAct with FRED, BLS, Census, HUD)
  Phase 3: Magnitude estimation (ReAct with code_execute)
  Phase 4: Distributional & temporal analysis (ReAct with code_execute)
  Phase 5: Final scorecard & report (ReAct with code_execute)

Both agents have conditional early-exit after Phase 1 if the policy
has no meaningful impact on their sector.
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from typing import Any, Awaitable, Callable

from backend.models.pipeline import (
    SectorReport,
)
from backend.pipeline.orchestrator import PipelineState

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

SECTORS = ["housing", "consumer"]


# ---------------------------------------------------------------------------
# Error report (when agent fails)
# ---------------------------------------------------------------------------

def _error_report(sector: str, error_msg: str) -> SectorReport:
    """Create a SectorReport that signals an agent failure.

    The synthesis agent will see empty effects and the error in dissent,
    allowing it to handle the gap transparently.
    """
    return SectorReport(
        sector=sector,
        direct_effects=[],
        second_order_effects=[],
        feedback_loops=[],
        cross_sector_dependencies=[s for s in SECTORS if s != sector],
        dissent=f"⚠️ AGENT ERROR — {sector} analysis unavailable: {error_msg}",
    )


# ---------------------------------------------------------------------------
# Phase descriptions for SSE events
# ---------------------------------------------------------------------------

HOUSING_PHASES: dict[str, dict[str, str]] = {
    "phase_1_pathways":      {"phase": "1", "label": "Identifying transmission pathways",      "detail": "Reasoning about how policy connects to housing"},
    "phase_2_baseline":      {"phase": "2", "label": "Gathering housing market baseline",      "detail": "Pulling FRED, BLS, Census, HUD data"},
    "phase_3_magnitudes":    {"phase": "3", "label": "Estimating impact magnitudes",           "detail": "Running elasticity + scenario models"},
    "phase_4_distributional":{"phase": "4", "label": "Distributional & temporal analysis",     "detail": "Who wins, who loses, and when"},
    "phase_5_scorecard":     {"phase": "5", "label": "Building affordability scorecard",       "detail": "Final housing report with sub-market impacts"},
}

CONSUMER_PHASES: dict[str, dict[str, str]] = {
    "phase_1_shock":           {"phase": "1", "label": "Identifying price shock entry points", "detail": "Where does the cost shock enter the economy?"},
    "phase_2_passthrough":     {"phase": "2", "label": "Estimating pass-through rates",        "detail": "Pulling CPI, PPI data + market structure analysis"},
    "phase_3_geo_behavioral":  {"phase": "3", "label": "Geographic & behavioral analysis",     "detail": "Regional price impacts + substitution patterns"},
    "phase_4_purchasing_power":{"phase": "4", "label": "Net purchasing power calculation",     "detail": "Income gains vs cost increases per household type"},
    "phase_5_scorecard":       {"phase": "5", "label": "Building consumer impact scorecard",   "detail": "Final consumer report with household-level impacts"},
}


# ---------------------------------------------------------------------------
# LangGraph sector agent runner
# ---------------------------------------------------------------------------

async def _run_langgraph_sector(
    sector: str,
    state: PipelineState,
    emit: EventCallback,
) -> SectorReport:
    """Run a LangGraph multi-phase sector agent (housing or consumer).

    Uses graph.astream_events() for real-time SSE events, with fallback
    to graph.astream() if event streaming isn't available.
    """
    from backend.pipeline.langgraph_adapter import (
        briefing_dict_to_analyst_briefing,
        housing_report_to_sector_report,
        consumer_report_to_sector_report,
    )

    await emit({
        "type": "sector_agent_started",
        "agent": sector,
        "data": {
            "sector": sector,
            "agent": sector.title(),
            "agent_mode": "agentic",
        },
    })

    try:
        # Bridge pipeline briefing dict → AnalystBriefing Pydantic model
        analyst_briefing = briefing_dict_to_analyst_briefing(state.briefing)
        logger.info(f"Sector {sector}: ✅ AnalystBriefing bridge built successfully")

        policy_query = state.query
        if analyst_briefing.policy_spec:
            policy_query = f"{analyst_briefing.policy_spec.action}: {analyst_briefing.policy_spec.value}"

        # Build graph and initial state based on sector
        if sector == "housing":
            from backend.agents.housing.graph import build_housing_graph
            graph = build_housing_graph()
            phase_map = HOUSING_PHASES
            convert_fn = housing_report_to_sector_report
        elif sector == "consumer":
            from backend.agents.consumer.graph import build_consumer_graph
            graph = build_consumer_graph()
            phase_map = CONSUMER_PHASES
            convert_fn = consumer_report_to_sector_report
        else:
            return _error_report(sector, f"No LangGraph agent for sector '{sector}'")

        initial: dict = {
            "analyst_briefing": analyst_briefing,
            "policy_query": policy_query,
            "current_phase": 1,
            "phase_1_output": None,
            "phase_2_output": None,
            "phase_3_output": None,
            "phase_4_output": None,
            "phase_5_output": None,
            "tool_call_log": [],
        }

        logger.info(f"Sector {sector}: 🚀 STARTING LangGraph 5-phase agent")

        async def _emit_thinking(step_type: str, content: str, **extra: Any) -> None:
            await emit({
                "type": "sector_agent_thinking",
                "agent": sector,
                "data": {
                    "agent": sector.title(),
                    "step_type": step_type,
                    "content": content,
                    **extra,
                },
            })

        await _emit_thinking("phase_start", "Starting 5-phase agentic pipeline", phase="0")

        final_state = {}
        current_phase = "1"
        seen_tool_ids: set[str] = set()

        try:
            async for event in graph.astream_events(initial, version="v2"):
                kind = event.get("event", "")
                name = event.get("name", "")
                run_id = event.get("run_id", "")

                if kind == "on_chain_start" and name in phase_map:
                    phase_info = phase_map[name]
                    current_phase = phase_info["phase"]
                    logger.info(f"Sector {sector}: Phase {current_phase} starting — {phase_info['label']}")
                    await emit({
                        "type": "sector_agent_tool_call",
                        "agent": sector,
                        "data": {
                            "agent": sector.title(),
                            "tool": f"phase_{current_phase}",
                            "query": phase_info["label"],
                            "phase": current_phase,
                            "phase_detail": phase_info["detail"],
                        },
                    })
                    await _emit_thinking("phase_start", phase_info["detail"], phase=current_phase)

                elif kind == "on_chain_end" and name in phase_map:
                    phase_info = phase_map[name]
                    output = event.get("data", {}).get("output", {})
                    if isinstance(output, dict):
                        final_state.update(output)
                    logger.info(f"Sector {sector}: Phase {phase_info['phase']} ✅ complete")
                    await _emit_thinking("phase_complete", f"Phase {phase_info['phase']} complete: {phase_info['label']}", phase=phase_info["phase"])

                elif kind == "on_tool_start":
                    tool_input = event.get("data", {}).get("input", {})
                    tool_id = f"{run_id}:{name}"
                    if tool_id not in seen_tool_ids:
                        seen_tool_ids.add(tool_id)
                        if isinstance(tool_input, dict):
                            args_str = ", ".join(f"{k}={v}" for k, v in list(tool_input.items())[:3])
                        else:
                            args_str = str(tool_input)[:100]
                        await _emit_thinking("tool_call", f"Calling {name}({args_str})", phase=current_phase, tool=name)

                elif kind == "on_tool_end":
                    tool_output = event.get("data", {}).get("output", "")
                    output_str = str(tool_output)[:150] if tool_output else "no data"
                    await _emit_thinking("tool_result", f"{name} → {output_str}", phase=current_phase, tool=name)

                elif kind == "on_chat_model_start":
                    await _emit_thinking("reasoning", "Analyzing data and forming conclusions...", phase=current_phase)

                elif kind == "on_chat_model_stream":
                    chunk_content = event.get("data", {})
                    if hasattr(chunk_content, "content") and chunk_content.content:
                        text = chunk_content.content if isinstance(chunk_content.content, str) else str(chunk_content.content)
                        if len(text) > 30:
                            await _emit_thinking("reasoning_chunk", text[:200], phase=current_phase)

        except Exception as stream_err:
            logger.warning(f"Sector {sector}: astream_events failed ({stream_err}), falling back to astream")
            await _emit_thinking("reasoning", "Switching to batch processing mode...", phase=current_phase)

            async for chunk in graph.astream(initial):
                for node_name, node_output in chunk.items():
                    final_state.update(node_output)
                    phase_info = phase_map.get(node_name)
                    if phase_info:
                        current_phase = phase_info["phase"]
                        await emit({
                            "type": "sector_agent_tool_call",
                            "agent": sector,
                            "data": {
                                "agent": sector.title(),
                                "tool": f"phase_{current_phase}",
                                "query": phase_info["label"],
                                "phase": current_phase,
                                "phase_detail": phase_info["detail"],
                            },
                        })
                        await _emit_thinking("phase_complete", phase_info["label"], phase=current_phase)

        # Extract the final report
        final_report = final_state.get("phase_5_output")
        if final_report is None:
            error_msg = "LangGraph produced no phase_5_output after all phases"
            logger.error(f"Sector {sector}: ❌ {error_msg}")
            return _error_report(sector, error_msg)

        all_tool_records = final_state.get("tool_call_log", [])
        logger.info(f"Sector {sector}: ✅ LangGraph complete with {len(all_tool_records)} tool calls")

        report = convert_fn(final_report, all_tool_records)
        # Preserve the original rich report so synthesis can access
        # phase-level outputs (scorecards, magnitudes, pathways).
        report._agentic_report = final_report  # type: ignore[attr-defined]
        logger.info(
            f"Sector {sector}: ✅ AGENTIC FLOW SUCCESS — "
            f"{len(report.direct_effects)} direct, "
            f"{len(report.second_order_effects)} second-order, "
            f"{len(all_tool_records)} tool calls"
        )
        return report

    except Exception:
        error_msg = traceback.format_exc()
        logger.error(f"Sector {sector}: ❌ LANGGRAPH AGENT FAILED:\n{error_msg}")
        return _error_report(sector, f"Agent crashed: {error_msg[:300]}")


# ---------------------------------------------------------------------------
# SSE event + eval for completed sector agents
# ---------------------------------------------------------------------------

async def _emit_sector_complete(sector: str, report: SectorReport, emit: EventCallback, state: PipelineState | None = None) -> None:
    """Emit the sector_agent_complete SSE event."""
    report_dict = report.model_dump()
    for claim_list_key in ("direct_effects", "second_order_effects", "feedback_loops"):
        for claim_dict in report_dict.get(claim_list_key, []):
            if "confidence" in claim_dict:
                claim_dict["confidence"] = claim_dict["confidence"].upper()

    await emit({
        "type": "sector_agent_complete",
        "agent": sector,
        "data": {
            "agent": sector.title(),
            "report": report_dict,
            "sector": sector,
            "agent_mode": report.agent_mode,
            "direct_effects": len(report.direct_effects),
            "second_order_effects": len(report.second_order_effects),
            "feedback_loops": len(report.feedback_loops),
            "has_dissent": report.dissent is not None,
            "tool_calls": len(report.tool_calls_made),
        },
    })

    # Fire eval in background (non-blocking)
    try:
        from backend.pipeline.eval_runner import evaluate_agent_output
        policy_type = ""
        income_effect = None
        if state and state.briefing:
            policy_type = state.briefing.get("policy_type", "") if isinstance(state.briefing, dict) else ""
            income_effect = state.briefing.get("income_effect_exists") if isinstance(state.briefing, dict) else None
        asyncio.create_task(
            evaluate_agent_output(sector, report, emit, policy_type=policy_type, income_effect=income_effect)
        )
    except Exception as e:
        logger.debug(f"Eval for {sector} skipped: {e}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_sector_agents(state: PipelineState, emit: EventCallback) -> PipelineState:
    """Stage 2: Run housing and consumer sector agents in parallel."""

    async def _run_sector(sector: str) -> SectorReport:
        report = await _run_langgraph_sector(sector, state, emit)
        await _emit_sector_complete(sector, report, emit, state=state)
        return report

    tasks = [_run_sector(sector) for sector in SECTORS]
    reports = await asyncio.gather(*tasks, return_exceptions=True)

    state.sector_reports = []
    for sector, result in zip(SECTORS, reports):
        if isinstance(result, Exception):
            logger.error(f"Sector {sector} failed: {result}")
            error = _error_report(sector, f"Agent crashed: {str(result)[:300]}")
            state.sector_reports.append(error)
        else:
            state.sector_reports.append(result)

    return state
