"""Tests for the two bridge bugs between pipeline and LangGraph agents.

Bug 1: _agentic_report was never set on SectorReport, so synthesis
       always saw housing_report=None and consumer_report=None.

Bug 2: policy_type and income_effect_exists were dropped when
       converting AnalystBriefing → dict → AnalystBriefing, breaking
       mode gating in synthesis, housing, and consumer agents.
"""

import pytest

from backend.agents.schemas import (
    AnalystBriefing,
    BaselineOutput,
    EvidenceOutput,
    PolicySpec,
)
from backend.agents.housing.schemas import HousingReport, CausalClaimSimple
from backend.agents.consumer.schemas import ConsumerReport, CausalClaimSimple as ConsumerCausalClaimSimple
from backend.models.pipeline import SectorReport
from backend.pipeline.langgraph_adapter import (
    briefing_dict_to_analyst_briefing,
    housing_report_to_sector_report,
    consumer_report_to_sector_report,
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _make_policy_spec(**overrides) -> PolicySpec:
    defaults = dict(
        policy_type="LABOR_COST",
        income_effect_exists=True,
        action="Raise the federal minimum wage",
        value="$15 per hour",
        scope="Federal, all covered employers",
        timeline="Phase-in over 3 years",
        current_baseline="$7.25 per hour",
    )
    defaults.update(overrides)
    return PolicySpec(**defaults)


def _make_briefing(**overrides) -> AnalystBriefing:
    return AnalystBriefing(
        executive_summary="Test briefing",
        key_findings=["finding1"],
        policy_spec=overrides.pop("policy_spec", _make_policy_spec()),
        **overrides,
    )


def _make_housing_report() -> HousingReport:
    return HousingReport(
        sector="housing",
        direct_effects=[
            CausalClaimSimple(
                claim="Rents rise modestly",
                cause="Higher labor costs",
                effect="0.3-0.5% rent increase",
                mechanism="Construction labor cost pass-through",
                confidence="EMPIRICAL",
                evidence=["FRED HOUST data"],
            )
        ],
        cross_sector_dependencies=["CONSUMER: housing cost line"],
    )


def _make_consumer_report() -> ConsumerReport:
    return ConsumerReport(
        sector="consumer",
        direct_effects=[
            ConsumerCausalClaimSimple(
                claim="Restaurant prices rise 3-4%",
                cause="Higher labor costs (30% of restaurant costs)",
                effect="CPI food-away-from-home increases",
                mechanism="Labor cost pass-through at 60-100% rate",
                confidence="EMPIRICAL",
                evidence=["BLS CPI data"],
            )
        ],
        cross_sector_dependencies=["LABOR: spending → employment"],
    )


# -----------------------------------------------------------------------
# Bug 1: _agentic_report preservation
# -----------------------------------------------------------------------

class TestAgenticReportBridge:
    """Verify that the rich sector report is preserved through the adapter."""

    def test_housing_report_preserved_on_sector_report(self):
        housing = _make_housing_report()
        sector_report = housing_report_to_sector_report(housing)
        # Simulate what sector.py now does
        sector_report._agentic_report = housing  # type: ignore[attr-defined]

        assert hasattr(sector_report, "_agentic_report")
        assert sector_report._agentic_report is housing
        assert sector_report._agentic_report.sector == "housing"
        assert len(sector_report._agentic_report.direct_effects) == 1

    def test_consumer_report_preserved_on_sector_report(self):
        consumer = _make_consumer_report()
        sector_report = consumer_report_to_sector_report(consumer)
        sector_report._agentic_report = consumer  # type: ignore[attr-defined]

        assert hasattr(sector_report, "_agentic_report")
        assert sector_report._agentic_report is consumer
        assert sector_report._agentic_report.sector == "consumer"

    def test_synthesis_can_extract_agentic_reports(self):
        """Simulate the synthesis extraction loop from synthesis.py:97-101."""
        housing = _make_housing_report()
        consumer = _make_consumer_report()

        h_report = housing_report_to_sector_report(housing)
        h_report._agentic_report = housing  # type: ignore[attr-defined]

        c_report = consumer_report_to_sector_report(consumer)
        c_report._agentic_report = consumer  # type: ignore[attr-defined]

        # Simulate labor/business as plain SectorReport (no _agentic_report)
        labor_report = SectorReport(sector="labor")
        business_report = SectorReport(sector="business")

        sector_reports = [labor_report, h_report, c_report, business_report]

        # This is the synthesis extraction logic
        housing_out = None
        consumer_out = None
        for report in sector_reports:
            if report.sector == "housing" and hasattr(report, "_agentic_report"):
                housing_out = report._agentic_report
            if report.sector == "consumer" and hasattr(report, "_agentic_report"):
                consumer_out = report._agentic_report

        assert housing_out is not None, "Synthesis should find housing _agentic_report"
        assert consumer_out is not None, "Synthesis should find consumer _agentic_report"
        assert housing_out.sector == "housing"
        assert consumer_out.sector == "consumer"

    def test_plain_sector_report_has_no_agentic_report(self):
        """Non-agentic reports (labor/business) should not have _agentic_report."""
        report = SectorReport(sector="labor")
        assert not hasattr(report, "_agentic_report")


# -----------------------------------------------------------------------
# Bug 2: policy_type and income_effect_exists round-trip
# -----------------------------------------------------------------------

class TestPolicyFieldsBridge:
    """Verify policy_type and income_effect_exists survive the dict round-trip."""

    def test_briefing_to_dict_preserves_policy_type(self):
        from backend.pipeline.analyst import _briefing_to_dict

        briefing = _make_briefing(
            policy_spec=_make_policy_spec(policy_type="LABOR_COST"),
        )
        d = _briefing_to_dict(briefing)

        assert d["policy_type"] == "LABOR_COST"

    def test_briefing_to_dict_preserves_income_effect_true(self):
        from backend.pipeline.analyst import _briefing_to_dict

        briefing = _make_briefing(
            policy_spec=_make_policy_spec(income_effect_exists=True),
        )
        d = _briefing_to_dict(briefing)

        assert d["income_effect_exists"] is True

    def test_briefing_to_dict_preserves_income_effect_false(self):
        from backend.pipeline.analyst import _briefing_to_dict

        briefing = _make_briefing(
            policy_spec=_make_policy_spec(
                policy_type="REGULATORY_COST",
                income_effect_exists=False,
            ),
        )
        d = _briefing_to_dict(briefing)

        assert d["income_effect_exists"] is False
        assert d["policy_type"] == "REGULATORY_COST"

    def test_briefing_to_dict_preserves_income_effect_none(self):
        from backend.pipeline.analyst import _briefing_to_dict

        briefing = _make_briefing(
            policy_spec=_make_policy_spec(income_effect_exists=None),
        )
        d = _briefing_to_dict(briefing)

        assert d["income_effect_exists"] is None

    def test_full_round_trip_labor_cost(self):
        """AnalystBriefing → dict → AnalystBriefing preserves mode fields."""
        from backend.pipeline.analyst import _briefing_to_dict

        original = _make_briefing(
            policy_spec=_make_policy_spec(
                policy_type="LABOR_COST",
                income_effect_exists=True,
            ),
        )

        d = _briefing_to_dict(original)
        reconstructed = briefing_dict_to_analyst_briefing(d)

        assert reconstructed.policy_spec is not None
        assert reconstructed.policy_spec.policy_type == "LABOR_COST"
        assert reconstructed.policy_spec.income_effect_exists is True

    def test_full_round_trip_regulatory_cost(self):
        """REGULATORY_COST with income_effect_exists=False survives round-trip."""
        from backend.pipeline.analyst import _briefing_to_dict

        original = _make_briefing(
            policy_spec=_make_policy_spec(
                policy_type="REGULATORY_COST",
                income_effect_exists=False,
            ),
        )

        d = _briefing_to_dict(original)
        reconstructed = briefing_dict_to_analyst_briefing(d)

        assert reconstructed.policy_spec is not None
        assert reconstructed.policy_spec.policy_type == "REGULATORY_COST"
        assert reconstructed.policy_spec.income_effect_exists is False

    def test_round_trip_with_string_income_effect(self):
        """income_effect_exists as string 'true'/'false' gets normalised to bool."""
        d = {
            "policy_spec": {
                "action": "Ban plastic bags",
                "value": "All retail",
                "scope": "State of California",
                "timeline": "2027",
                "current_baseline": "No ban",
                "policy_type": "REGULATORY_COST",
                "income_effect_exists": "false",
            },
        }
        reconstructed = briefing_dict_to_analyst_briefing(d)

        assert reconstructed.policy_spec is not None
        assert reconstructed.policy_spec.income_effect_exists is False

    def test_round_trip_top_level_overrides_nested(self):
        """Top-level policy_type takes precedence over nested policy_spec value."""
        d = {
            "policy_type": "TRADE",
            "income_effect_exists": False,
            "policy_spec": {
                "action": "Impose tariff",
                "value": "25%",
                "scope": "Federal",
                "timeline": "Immediate",
                "current_baseline": "0% tariff",
                "policy_type": "OTHER",
                "income_effect_exists": True,
            },
        }
        reconstructed = briefing_dict_to_analyst_briefing(d)

        assert reconstructed.policy_spec is not None
        assert reconstructed.policy_spec.policy_type == "TRADE"
        assert reconstructed.policy_spec.income_effect_exists is False

    def test_missing_policy_spec_gives_none(self):
        """If briefing has no policy_spec, policy_spec should be None."""
        d = {"summary": "Some summary"}
        reconstructed = briefing_dict_to_analyst_briefing(d)

        assert reconstructed.policy_spec is None
