"""Live end-to-end test of the bridge fixes.

Runs the full pipeline (classifier → analyst → sector → synthesis) via
the orchestrator with a real query and real API calls. Captures every
SSE event and validates:
  1. Synthesis receives non-None housing/consumer reports (_agentic_report)
  2. policy_type and income_effect_exists survive the round-trip
  3. Per-stage and per-tool latencies
"""

import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bridge_live")


async def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "Raise the federal minimum wage to $15/hr"

    print("=" * 70)
    print(f"LIVE BRIDGE TEST: {query}")
    print("=" * 70)

    events: list[dict] = []
    event_times: list[float] = []

    async def capture_event(event: dict) -> None:
        events.append(event)
        event_times.append(time.time())
        etype = event.get("type", "?")
        agent = event.get("agent", "")
        data = event.get("data", {})

        # Compact log of each event
        if etype == "classifier_complete":
            print(f"  [{etype}] task_type={data.get('task_type')}")
        elif etype == "analyst_thinking":
            step = data.get("step_type", "")
            phase = data.get("phase", "")
            content = data.get("content", "")[:100]
            if step in ("phase_start", "phase_complete", "tool_call"):
                print(f"  [{etype}] phase={phase} {step}: {content}")
        elif etype == "analyst_complete":
            mode = data.get("mode", "?")
            tools = data.get("tools_called", data.get("tool_calls_made", 0))
            print(f"  [{etype}] mode={mode}, tools={tools}")
        elif etype == "sector_agent_started":
            print(f"  [{etype}] {data.get('sector')} (mode={data.get('agent_mode', '?')})")
        elif etype == "sector_agent_complete":
            sector = data.get("sector", "?")
            mode = data.get("agent_mode", "?")
            de = data.get("direct_effects", 0)
            tc = data.get("tool_calls", 0)
            print(f"  [{etype}] {sector}: mode={mode}, effects={de}, tools={tc}")
        elif etype == "synthesis_thinking":
            step = data.get("step_type", "")
            phase = data.get("phase", "")
            content = data.get("content", "")[:120]
            if step in ("phase_start", "phase_complete"):
                print(f"  [{etype}] phase={phase} {step}: {content}")
        elif etype == "synthesis_complete":
            print(f"  [{etype}] DONE")
        elif etype == "pipeline_complete":
            times = data.get("stage_times", {})
            print(f"  [{etype}] total={data.get('total_seconds')}s, stages={json.dumps(times)}")
        elif etype == "pipeline_error":
            print(f"  [ERROR] {data.get('error', '?')[:200]}")

    # ---------------------------------------------------------------
    # Run the pipeline
    # ---------------------------------------------------------------
    from backend.pipeline.orchestrator import run_pipeline

    t_start = time.time()
    state = await run_pipeline(query=query, emit=capture_event)
    t_total = time.time() - t_start

    # ---------------------------------------------------------------
    # Validate Bug 1: _agentic_report
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("VALIDATION: Bug 1 — _agentic_report bridge")
    print("=" * 70)

    housing_agentic = None
    consumer_agentic = None
    for report in state.sector_reports:
        if report.sector == "housing":
            has_attr = hasattr(report, "_agentic_report")
            print(f"  housing SectorReport: has _agentic_report = {has_attr}")
            if has_attr:
                housing_agentic = report._agentic_report
                print(f"    type: {type(housing_agentic).__name__}")
                print(f"    direct_effects: {len(housing_agentic.direct_effects)}")
                print(f"    pathway_analysis: {housing_agentic.pathway_analysis is not None}")
                print(f"    housing_baseline: {housing_agentic.housing_baseline is not None}")
                print(f"    magnitude_estimates: {housing_agentic.magnitude_estimates is not None}")
                print(f"    affordability_scorecard: {housing_agentic.affordability_scorecard is not None}")
                if housing_agentic.affordability_scorecard and housing_agentic.affordability_scorecard.sub_markets:
                    for sm in housing_agentic.affordability_scorecard.sub_markets:
                        print(f"      scorecard: {sm.region_name} rent_change={sm.rent_change}")
            else:
                print(f"    agent_mode: {report.agent_mode}")

        if report.sector == "consumer":
            has_attr = hasattr(report, "_agentic_report")
            print(f"  consumer SectorReport: has _agentic_report = {has_attr}")
            if has_attr:
                consumer_agentic = report._agentic_report
                print(f"    type: {type(consumer_agentic).__name__}")
                print(f"    direct_effects: {len(consumer_agentic.direct_effects)}")
                print(f"    price_shock_analysis: {consumer_agentic.price_shock_analysis is not None}")
                print(f"    purchasing_power: {consumer_agentic.purchasing_power is not None}")
                print(f"    consumer_impact_scorecard: {consumer_agentic.consumer_impact_scorecard is not None}")
            else:
                print(f"    agent_mode: {report.agent_mode}")

    if housing_agentic:
        print("\n  ✅ Housing: _agentic_report preserved through bridge")
    else:
        print("\n  ⚠️  Housing: no _agentic_report (may have fallen back to single-shot)")

    if consumer_agentic:
        print("  ✅ Consumer: _agentic_report preserved through bridge")
    else:
        print("  ⚠️  Consumer: no _agentic_report (may have fallen back to single-shot)")

    # ---------------------------------------------------------------
    # Validate Bug 2: policy_type / income_effect_exists
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("VALIDATION: Bug 2 — policy_type / income_effect_exists")
    print("=" * 70)

    briefing = state.briefing
    print(f"  state.briefing['policy_type'] = {briefing.get('policy_type')!r}")
    print(f"  state.briefing['income_effect_exists'] = {briefing.get('income_effect_exists')!r}")

    # Check nested spec too
    ps = briefing.get("policy_spec", {})
    if isinstance(ps, dict):
        print(f"  state.briefing['policy_spec']['policy_type'] = {ps.get('policy_type')!r}")
        print(f"  state.briefing['policy_spec']['income_effect_exists'] = {ps.get('income_effect_exists')!r}")

    # Reconstruct what synthesis would see
    from backend.pipeline.langgraph_adapter import briefing_dict_to_analyst_briefing
    reconstructed = briefing_dict_to_analyst_briefing(briefing)
    if reconstructed.policy_spec:
        print(f"\n  Reconstructed for synthesis:")
        print(f"    policy_type = {reconstructed.policy_spec.policy_type!r}")
        print(f"    income_effect_exists = {reconstructed.policy_spec.income_effect_exists!r}")
        if reconstructed.policy_spec.policy_type:
            print("  ✅ policy_type survived round-trip")
        else:
            print("  ❌ policy_type is empty after round-trip")
        if reconstructed.policy_spec.income_effect_exists is not None:
            print("  ✅ income_effect_exists survived round-trip")
        else:
            print("  ⚠️  income_effect_exists is None (analyst may not have set it)")
    else:
        print("  ⚠️  No policy_spec reconstructed")

    # ---------------------------------------------------------------
    # Validate synthesis output
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("VALIDATION: Synthesis output")
    print("=" * 70)

    if state.synthesis:
        syn = state.synthesis
        print(f"  policy_summary: {syn.policy_summary.policy_name[:80] if syn.policy_summary else 'None'}")
        print(f"  agreed_findings: {len(syn.agreed_findings)}")
        print(f"  disagreements: {len(syn.disagreements)}")
        print(f"  sector_reports: {len(syn.sector_reports)}")
        if syn.unified_impact:
            print(f"  unified_impact.summary: {syn.unified_impact.summary[:150]}")
            print(f"  key_findings: {len(syn.unified_impact.key_findings)}")
        if syn.sankey_data:
            print(f"  sankey: {len(syn.sankey_data.nodes)} nodes, {len(syn.sankey_data.links)} links")
    else:
        print("  ⚠️  No synthesis report produced")

    # ---------------------------------------------------------------
    # Latency breakdown
    # ---------------------------------------------------------------
    print("\n" + "=" * 70)
    print("LATENCY BREAKDOWN")
    print("=" * 70)
    print(f"  Total pipeline: {t_total:.1f}s")
    for stage, duration in state.stage_times.items():
        print(f"  {stage:20s}: {duration:.1f}s")

    # Count events by type
    event_counts: dict[str, int] = {}
    for e in events:
        t = e.get("type", "?")
        event_counts[t] = event_counts.get(t, 0) + 1
    print(f"\n  SSE events emitted: {len(events)}")
    for t, c in sorted(event_counts.items()):
        print(f"    {t}: {c}")

    print(f"\n  Tool calls recorded: {len(state.tool_calls)}")
    print(f"  Sector reports: {len(state.sector_reports)}")
    for r in state.sector_reports:
        print(f"    {r.sector}: mode={r.agent_mode}, effects={len(r.direct_effects)}+{len(r.second_order_effects)}")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
