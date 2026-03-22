"""
Test: classifier output flows correctly to analyst.

Verifies:
1. policy_type from ADK maps to a real SEARCH_STRATEGIES key (not default)
2. cleaned_query is propagated to state.query
3. policy_params are available in state.briefing after analyst runs

Run from project root:
    python test_classifier_analyst_flow.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import AsyncMock, patch

from backend.pipeline.orchestrator import PipelineState
from backend.pipeline.analyst import SEARCH_STRATEGIES, DEFAULT_STRATEGY
from backend.agents.schemas import ClassifierOutput, PolicyTaskType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _noop_emit(event):
    pass


def make_adk_output(task_type: PolicyTaskType, cleaned_query: str) -> ClassifierOutput:
    return ClassifierOutput(
        task_type=task_type,
        policy_params={"action": "test", "value": "test", "scope": "US", "timeline": "immediate"},
        confidence="high",
        cleaned_query=cleaned_query,
        reasoning="test classification",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_policy_type_maps_to_search_strategy():
    """Each ADK task_type should resolve to a specific (non-default) strategy."""
    cases = [
        (PolicyTaskType.IMMIGRATION,   "Restrict H-1B visas for tech workers",             "immigration"),
        (PolicyTaskType.TRADE_TARIFF,  "Impose 25% tariff on Chinese electronics",          "trade_tariff"),
        (PolicyTaskType.EDUCATION,     "Forgive $50k of student loan debt per borrower",    "education"),
        (PolicyTaskType.MINIMUM_WAGE,  "Raise the federal minimum wage to $15/hr",          "minimum_wage"),
    ]

    from backend.pipeline.classifier import run_classifier

    for task_type, raw_query, expected_key in cases:
        state = PipelineState(query=raw_query)
        adk_output = make_adk_output(task_type, f"Cleaned: {raw_query}")

        with patch("backend.agents.classifier.run_classifier", new=AsyncMock(return_value=adk_output)):
            state = await run_classifier(state, _noop_emit)

        assert state.policy_type == expected_key, (
            f"[FAIL] query='{raw_query}': expected policy_type='{expected_key}', got '{state.policy_type}'"
        )
        assert state.policy_type in SEARCH_STRATEGIES, (
            f"[FAIL] policy_type='{state.policy_type}' has no entry in SEARCH_STRATEGIES — would fall to default"
        )
        print(f"[PASS] {task_type.value} → strategy key '{state.policy_type}' ✓")


async def test_cleaned_query_propagated():
    """state.query should be updated to cleaned_query after classifier runs."""
    raw = "raise min wage"
    cleaned = "Raise the federal minimum wage to $15 per hour nationally"

    from backend.pipeline.classifier import run_classifier

    state = PipelineState(query=raw)
    adk_output = make_adk_output(PolicyTaskType.MINIMUM_WAGE, cleaned)

    with patch("backend.agents.classifier.run_classifier", new=AsyncMock(return_value=adk_output)):
        state = await run_classifier(state, _noop_emit)

    assert state.query == cleaned, (
        f"[FAIL] state.query not updated: expected '{cleaned}', got '{state.query}'"
    )
    print(f"[PASS] cleaned_query propagated to state.query ✓")


async def test_policy_params_in_briefing():
    """policy_params from classifier should appear in state.briefing after analyst."""
    from backend.pipeline.classifier import run_classifier
    from backend.pipeline.analyst import run_analyst

    raw = "Raise the federal minimum wage to $15/hr"
    state = PipelineState(query=raw)
    adk_output = make_adk_output(PolicyTaskType.MINIMUM_WAGE, raw)

    with patch("backend.agents.classifier.run_classifier", new=AsyncMock(return_value=adk_output)):
        state = await run_classifier(state, _noop_emit)

    # Patch all tool calls to return empty dicts so we don't need API keys
    with patch("backend.pipeline.analyst._call_tool", new=AsyncMock(return_value={})):
        with patch("backend.pipeline.analyst._summarize_briefing", new=AsyncMock(return_value="mock summary")):
            state = await run_analyst(state, _noop_emit)

    assert "policy_params" in state.briefing, "[FAIL] policy_params missing from state.briefing"
    assert state.briefing["policy_params"].get("policy_type") == "minimum_wage", (
        f"[FAIL] wrong policy_type in briefing: {state.briefing['policy_params']}"
    )
    print(f"[PASS] policy_params present in briefing with correct policy_type ✓")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main():
    print("\n=== Testing classifier → analyst flow ===\n")
    try:
        await test_policy_type_maps_to_search_strategy()
        await test_cleaned_query_propagated()
        await test_policy_params_in_briefing()
        print("\n✅ All tests passed.\n")
    except AssertionError as e:
        print(f"\n❌ {e}\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}\n")
        import traceback; traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
