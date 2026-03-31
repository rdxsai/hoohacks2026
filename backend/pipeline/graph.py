"""
Top-level LangGraph pipeline graph.

Replaces the sequential orchestrator with a single StateGraph:
  classifier → analyst → premium → housing+consumer (parallel) → synthesis

Streaming is handled by graph.astream_events() — no manual emit callbacks.
"""

from __future__ import annotations

import logging
import operator
import time
import uuid
from typing import Annotated, Any, TypedDict

from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from backend.agents.housing.schemas import HousingReport
from backend.agents.consumer.schemas import ConsumerReport
from backend.agents.schemas import AnalystBriefing
from backend.models.pipeline import SectorReport, SynthesisReport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: run a sub-graph and forward its events as custom events
# ---------------------------------------------------------------------------

async def _run_subgraph(
    graph,
    initial: dict,
    agent_name: str,
    config: RunnableConfig,
) -> dict:
    """Run a compiled sub-graph via astream_events, forwarding tool/phase events
    as custom events so they bubble up to the top-level SSE stream.

    Returns the final accumulated state from all node outputs.
    """
    final_state: dict = {}

    async for event in graph.astream_events(initial, version="v2", config=config):
        kind = event.get("event", "")
        name = event.get("name", "")
        data = event.get("data", {})

        # Accumulate state from node completions
        if kind == "on_chain_end":
            output = data.get("output")
            if isinstance(output, dict) and name not in ("LangGraph",):
                final_state.update(output)

        # Custom events auto-propagate through astream_events chain —
        # no re-dispatch needed. Just log for diagnostics.
        elif kind == "on_custom_event":
            logger.info(f"[_run_subgraph/{agent_name}] custom event seen: {name} (auto-propagates)")

    return final_state


# ---------------------------------------------------------------------------
# Pipeline state — flows through all nodes
# ---------------------------------------------------------------------------

class PipelineGraphState(TypedDict, total=False):
    # Input
    query: str
    user_context: dict[str, Any]
    session_id: str
    start_time: float

    # Stage 0: Classifier
    policy_type: str
    policy_params: dict[str, Any]

    # Stage 1: Analyst
    analyst_briefing: AnalystBriefing | None
    briefing_dict: dict[str, Any]

    # Stage 1.5: Premium
    premium_data: dict[str, Any]
    payments: list[dict[str, Any]]

    # Stage 2: Sector (list with operator.add reducer for parallel merge)
    sector_reports: Annotated[list[SectorReport], operator.add]
    housing_report: HousingReport | None
    consumer_report: ConsumerReport | None

    # Stage 3: Synthesis
    synthesis_report: SynthesisReport | None

    # Timing
    stage_times: dict[str, float]


# ---------------------------------------------------------------------------
# Node: Classifier
# ---------------------------------------------------------------------------

async def classifier_node(state: PipelineGraphState, config: RunnableConfig) -> dict:
    """Stage 0: Classify policy query and extract parameters."""
    t0 = time.time()

    await adispatch_custom_event("classifier_start", {"query": state["query"]}, config=config)

    from backend.pipeline.classifier import (
        CLASSIFIER_SYSTEM,
        _keyword_classify,
    )
    from backend.pipeline.llm import llm_chat, parse_json_response
    import json

    query = state["query"]
    result = None

    # LLM classification
    try:
        await adispatch_custom_event("classifier_thinking", {
            "step_type": "tool_call", "content": "Classifying via LLM", "phase": "1",
        }, config=config)

        raw = await llm_chat(
            system_prompt=CLASSIFIER_SYSTEM,
            user_prompt=f"Policy question: {query}\n\nUser context: {json.dumps(state.get('user_context', {}))}",
            json_mode=True,
            fast=True,
        )
        if raw:
            result = parse_json_response(raw)
            await adispatch_custom_event("classifier_thinking", {
                "step_type": "tool_result",
                "content": f"Classified as: {result.get('policy_type', 'unknown')}",
                "phase": "1",
            }, config=config)
    except Exception as exc:
        await adispatch_custom_event("classifier_thinking", {
            "step_type": "tool_result",
            "content": f"LLM failed ({type(exc).__name__}), trying keyword match",
            "phase": "1",
        }, config=config)

    # Keyword fallback
    if not result:
        result = _keyword_classify(query)

    # Generic fallback
    if not result:
        result = {
            "policy_type": "other",
            "policy_name": "Policy Analysis",
            "parameters": {"query": query},
            "affected_populations": ["general public"],
        }

    duration = time.time() - t0

    await adispatch_custom_event("classifier_complete", {
        "task_type": result.get("policy_type", "other"),
        "policy_params": result.get("parameters", {}),
        "affected_sectors": ["housing", "consumer"],
        "extracted_tags": result.get("affected_populations", []),
        "policy_type": result.get("policy_type", "other"),
        "policy_name": result.get("policy_name", ""),
    }, config=config)

    return {
        "policy_type": result.get("policy_type", "other"),
        "policy_params": result,
        "stage_times": {"classifier": round(duration, 1)},
    }


# ---------------------------------------------------------------------------
# Node: Analyst
# ---------------------------------------------------------------------------

async def analyst_node(state: PipelineGraphState, config: RunnableConfig) -> dict:
    """Stage 1: Run the 5-phase LangGraph analyst agent."""
    t0 = time.time()

    await adispatch_custom_event("agent_start", {
        "agent": "analyst", "mode": "agentic",
    }, config=config)

    from backend.agents.graph import AnalystState, build_analyst_graph
    from backend.pipeline.analyst import _briefing_to_dict

    graph = build_analyst_graph()

    initial: AnalystState = {
        "policy_query": state["query"],
        "current_phase": 1,
        "phase_1_output": None,
        "phase_2_output": None,
        "phase_3_output": None,
        "phase_4_output": None,
        "phase_5_output": None,
        "tool_call_log": [],
    }

    # Run sub-graph — tool/phase events forwarded via _run_subgraph
    final_state = await _run_subgraph(graph, initial, "analyst", config)

    briefing = final_state.get("phase_5_output")
    duration = time.time() - t0

    if briefing:
        briefing_dict = _briefing_to_dict(briefing)
        briefing_dict["policy_params"] = state.get("policy_params", {})

        await adispatch_custom_event("analyst_complete", {
            "mode": "agentic",
            "phases_completed": 5,
            "summary": briefing.executive_summary[:300] if briefing.executive_summary else "",
        }, config=config)

        return {
            "analyst_briefing": briefing,
            "briefing_dict": briefing_dict,
            "stage_times": {"analyst": round(duration, 1)},
        }

    # Fallback: simple analyst
    await adispatch_custom_event("analyst_thinking", {
        "step_type": "reasoning",
        "content": "LangGraph analyst failed, using simple fallback",
        "phase": "0",
    }, config=config)

    from backend.pipeline.analyst import _run_simple_analyst_standalone
    briefing_dict = await _run_simple_analyst_standalone(state["query"], state.get("policy_type", "other"), state.get("policy_params", {}))
    from backend.pipeline.langgraph_adapter import briefing_dict_to_analyst_briefing
    analyst_briefing = briefing_dict_to_analyst_briefing(briefing_dict)

    await adispatch_custom_event("analyst_complete", {
        "mode": "simple",
        "summary": briefing_dict.get("summary", "")[:300],
    }, config=config)

    return {
        "analyst_briefing": analyst_briefing,
        "briefing_dict": briefing_dict,
        "stage_times": {"analyst": round(time.time() - t0, 1)},
    }


# ---------------------------------------------------------------------------
# Node: Premium (L402 Lightning)
# ---------------------------------------------------------------------------

async def premium_node(state: PipelineGraphState, config: RunnableConfig) -> dict:
    """Stage 1.5: Premium data via L402 Lightning payments."""
    t0 = time.time()

    try:
        from backend.lightning.premium_agent import PremiumDataAgent

        async def on_event(event: dict) -> None:
            await adispatch_custom_event("lightning_payment", event, config=config)

        agent = PremiumDataAgent()
        result = await agent.run(query=state["query"], on_event=on_event)
        await agent.close()

        premium_data = result.get("premium_data", {})
        payments = result.get("payments", [])

        # Merge premium data into briefing
        briefing_dict = dict(state.get("briefing_dict", {}))
        if premium_data:
            briefing_dict["premium_data"] = premium_data

        return {
            "premium_data": premium_data,
            "payments": payments,
            "briefing_dict": briefing_dict,
            "stage_times": {"premium": round(time.time() - t0, 1)},
        }
    except Exception as e:
        logger.warning(f"Premium stage failed (non-fatal): {e}")
        return {
            "premium_data": {},
            "payments": [],
            "stage_times": {"premium": round(time.time() - t0, 1)},
        }


# ---------------------------------------------------------------------------
# Fan-out: Parallel sector agents via Send
# ---------------------------------------------------------------------------

def fan_out_sectors(state: PipelineGraphState) -> list[Send]:
    """Dispatch housing and consumer agents in parallel."""
    return [
        Send("housing_node", state),
        Send("consumer_node", state),
    ]


# ---------------------------------------------------------------------------
# Node: Housing Agent
# ---------------------------------------------------------------------------

async def housing_node(state: PipelineGraphState, config: RunnableConfig) -> dict:
    """Run the 5-phase housing LangGraph agent."""
    t0 = time.time()

    await adispatch_custom_event("sector_agent_started", {
        "agent": "Housing", "sector": "housing", "agent_mode": "agentic",
    }, config=config)

    try:
        from backend.agents.housing.graph import build_housing_graph
        from backend.pipeline.langgraph_adapter import (
            briefing_dict_to_analyst_briefing,
            housing_report_to_sector_report,
        )

        analyst_briefing = state.get("analyst_briefing")
        if not analyst_briefing:
            analyst_briefing = briefing_dict_to_analyst_briefing(state.get("briefing_dict", {}))

        policy_query = state["query"]
        if analyst_briefing.policy_spec:
            policy_query = f"{analyst_briefing.policy_spec.action}: {analyst_briefing.policy_spec.value}"

        graph = build_housing_graph()
        initial = {
            "analyst_briefing": analyst_briefing,
            "policy_query": policy_query,
            "current_phase": 1,
            "phase_1_output": None, "phase_2_output": None,
            "phase_3_output": None, "phase_4_output": None,
            "phase_5_output": None,
            "tool_call_log": [],
        }

        final = await _run_subgraph(graph, initial, "Housing", config)

        housing_report = final.get("phase_5_output")
        if housing_report is None:
            report = _make_error_report("housing", "No phase_5_output produced")
            await _emit_sector_complete("housing", report, config)
            return {"sector_reports": [report], "stage_times": {"housing": round(time.time() - t0, 1)}}

        tool_records = final.get("tool_call_log", [])
        report = housing_report_to_sector_report(housing_report, tool_records)
        report._agentic_report = housing_report  # type: ignore[attr-defined]

        await _emit_sector_complete("housing", report, config)

        return {
            "sector_reports": [report],
            "housing_report": housing_report,
            "stage_times": {"housing": round(time.time() - t0, 1)},
        }

    except Exception as e:
        logger.error(f"Housing agent failed: {e}")
        report = _make_error_report("housing", str(e)[:300])
        await _emit_sector_complete("housing", report, config)
        return {"sector_reports": [report], "stage_times": {"housing": round(time.time() - t0, 1)}}


# ---------------------------------------------------------------------------
# Node: Consumer Agent
# ---------------------------------------------------------------------------

async def consumer_node(state: PipelineGraphState, config: RunnableConfig) -> dict:
    """Run the 5-phase consumer LangGraph agent."""
    t0 = time.time()

    await adispatch_custom_event("sector_agent_started", {
        "agent": "Consumer", "sector": "consumer", "agent_mode": "agentic",
    }, config=config)

    try:
        from backend.agents.consumer.graph import build_consumer_graph
        from backend.pipeline.langgraph_adapter import (
            briefing_dict_to_analyst_briefing,
            consumer_report_to_sector_report,
        )

        analyst_briefing = state.get("analyst_briefing")
        if not analyst_briefing:
            analyst_briefing = briefing_dict_to_analyst_briefing(state.get("briefing_dict", {}))

        policy_query = state["query"]
        if analyst_briefing.policy_spec:
            policy_query = f"{analyst_briefing.policy_spec.action}: {analyst_briefing.policy_spec.value}"

        graph = build_consumer_graph()
        initial = {
            "analyst_briefing": analyst_briefing,
            "policy_query": policy_query,
            "current_phase": 1,
            "phase_1_output": None, "phase_2_output": None,
            "phase_3_output": None, "phase_4_output": None,
            "phase_5_output": None,
            "tool_call_log": [],
        }

        final = await _run_subgraph(graph, initial, "Consumer", config)

        consumer_report = final.get("phase_5_output")
        if consumer_report is None:
            report = _make_error_report("consumer", "No phase_5_output produced")
            await _emit_sector_complete("consumer", report, config)
            return {"sector_reports": [report], "stage_times": {"consumer": round(time.time() - t0, 1)}}

        tool_records = final.get("tool_call_log", [])
        report = consumer_report_to_sector_report(consumer_report, tool_records)
        report._agentic_report = consumer_report  # type: ignore[attr-defined]

        await _emit_sector_complete("consumer", report, config)

        return {
            "sector_reports": [report],
            "consumer_report": consumer_report,
            "stage_times": {"consumer": round(time.time() - t0, 1)},
        }

    except Exception as e:
        logger.error(f"Consumer agent failed: {e}")
        report = _make_error_report("consumer", str(e)[:300])
        await _emit_sector_complete("consumer", report, config)
        return {"sector_reports": [report], "stage_times": {"consumer": round(time.time() - t0, 1)}}


# ---------------------------------------------------------------------------
# Barrier: Join after parallel sectors
# ---------------------------------------------------------------------------

async def sector_barrier(state: PipelineGraphState) -> dict:
    """No-op join point — state already has merged sector_reports."""
    return {}


# ---------------------------------------------------------------------------
# Node: Synthesis
# ---------------------------------------------------------------------------

async def synthesis_node(state: PipelineGraphState, config: RunnableConfig) -> dict:
    """Stage 3: Run the 5-phase synthesis agent."""
    t0 = time.time()

    await adispatch_custom_event("agent_start", {
        "agent": "synthesis", "sectors_analyzed": len(state.get("sector_reports", [])),
    }, config=config)

    from backend.agents.synthesis.graph import build_synthesis_graph
    from backend.agents.synthesis.schemas import SynthesisState
    from backend.pipeline.langgraph_adapter import briefing_dict_to_analyst_briefing

    analyst_briefing = state.get("analyst_briefing")
    if not analyst_briefing:
        analyst_briefing = briefing_dict_to_analyst_briefing(state.get("briefing_dict", {}))

    # Extract rich reports for synthesis
    housing_report = state.get("housing_report")
    consumer_report = state.get("consumer_report")

    # Also check _agentic_report on sector_reports as fallback
    if not housing_report or not consumer_report:
        for report in state.get("sector_reports", []):
            if report.sector == "housing" and hasattr(report, "_agentic_report") and not housing_report:
                housing_report = report._agentic_report
            if report.sector == "consumer" and hasattr(report, "_agentic_report") and not consumer_report:
                consumer_report = report._agentic_report

    graph = build_synthesis_graph()

    initial: SynthesisState = {
        "analyst_briefing": analyst_briefing,
        "housing_report": housing_report,
        "consumer_report": consumer_report,
        "policy_query": state["query"],
        "current_phase": 1,
        "phase_1_output": None,
        "phase_2_output": None,
        "phase_3_output": None,
        "phase_4_output": None,
        "phase_5_output": None,
        "phase_1_summary": None,
        "phase_2_summary": None,
        "phase_3_summary": None,
        "tool_call_log": [],
    }

    final = await _run_subgraph(graph, initial, "synthesis", config)
    duration = time.time() - t0

    synthesis_output = final.get("phase_5_output")

    await adispatch_custom_event("synthesis_complete", {
        "has_report": synthesis_output is not None,
    }, config=config)

    return {
        "synthesis_report": synthesis_output,
        "stage_times": {"synthesis": round(duration, 1)},
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_error_report(sector: str, error_msg: str) -> SectorReport:
    return SectorReport(
        sector=sector,
        direct_effects=[],
        second_order_effects=[],
        feedback_loops=[],
        cross_sector_dependencies=[],
        dissent=f"⚠️ AGENT ERROR — {sector}: {error_msg}",
    )


async def _emit_sector_complete(sector: str, report: SectorReport, config: RunnableConfig) -> None:
    report_dict = report.model_dump()
    await adispatch_custom_event("sector_agent_complete", {
        "agent": sector.title(),
        "sector": sector,
        "report": report_dict,
        "agent_mode": report.agent_mode,
        "direct_effects": len(report.direct_effects),
        "second_order_effects": len(report.second_order_effects),
    }, config=config)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_pipeline_graph():
    """Build the top-level pipeline StateGraph."""
    graph = StateGraph(PipelineGraphState)

    graph.add_node("classifier_node", classifier_node)
    graph.add_node("analyst_node", analyst_node)
    graph.add_node("premium_node", premium_node)
    graph.add_node("housing_node", housing_node)
    graph.add_node("consumer_node", consumer_node)
    graph.add_node("sector_barrier", sector_barrier)
    graph.add_node("synthesis_node", synthesis_node)

    graph.add_edge(START, "classifier_node")
    graph.add_edge("classifier_node", "analyst_node")
    graph.add_edge("analyst_node", "premium_node")
    graph.add_conditional_edges("premium_node", fan_out_sectors, ["housing_node", "consumer_node"])
    graph.add_edge("housing_node", "sector_barrier")
    graph.add_edge("consumer_node", "sector_barrier")
    graph.add_edge("sector_barrier", "synthesis_node")
    graph.add_edge("synthesis_node", END)

    return graph.compile()
