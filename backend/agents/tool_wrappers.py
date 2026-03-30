from __future__ import annotations

import json
import logging
import time
import traceback

from langchain_core.tools import tool

_tool_logger = logging.getLogger("policypulse.tools")

# Per-call timing log: list of (tool_name, args_snippet, duration_ms, success)
# Reset per pipeline run. Read by _extract_tool_records for ToolCallRecord.duration_ms.
_tool_timings: list[tuple[str, str, float, bool]] = []


def get_and_clear_tool_timings() -> list[tuple[str, str, float, bool]]:
    """Return accumulated tool timings and reset the list."""
    global _tool_timings
    result = _tool_timings[:]
    _tool_timings = []
    return result


from backend.tools import (
    fred_search as _fred_search,
    fred_get_series as _fred_get_series,
    fred_get_many as _fred_get_many,
    bls_get_data as _bls_get_data,
    search_academic_papers as _search_academic_papers,
    search_openalex as _search_openalex,
    search_cbo_reports as _search_cbo_reports,
    web_search_news as _web_search_news,
    fetch_document_text as _fetch_document_text,
)


# ---------------------------------------------------------------------------
# Error-safe wrapper helper
# ---------------------------------------------------------------------------


def _error_json(tool_name: str, error: Exception) -> str:
    """Return a JSON error message the LLM can read and self-correct from."""
    return json.dumps(
        {
            "error": True,
            "tool": tool_name,
            "message": str(error),
            "hint": "Check your arguments and try again, or use a different tool/query.",
        },
        indent=2,
    )


def _record_timing(tool_name: str, args_snippet: str, duration_ms: float, success: bool) -> None:
    """Record a tool invocation's timing."""
    _tool_timings.append((tool_name, args_snippet, duration_ms, success))
    _tool_logger.info(f"TOOL {tool_name}({args_snippet[:60]}): {duration_ms:.0f}ms {'OK' if success else 'FAIL'}")


# ---------------------------------------------------------------------------
# LangChain tool wrappers
# Each wraps our async Pydantic-returning tool → JSON string for the LLM
# Errors are caught and returned as JSON so the LLM can self-correct
# ---------------------------------------------------------------------------


@tool
async def fred_search(query: str, limit: int = 5) -> str:
    """Search FRED for economic data series by keyword. Returns series IDs,
    titles, units, and frequencies. Use to discover which FRED series exist
    for a given economic indicator before calling fred_get_series."""
    t0 = time.perf_counter()
    try:
        result = await _fred_search(query=query, limit=limit)
        _record_timing("fred_search", query, (time.perf_counter() - t0) * 1000, True)
        return result.model_dump_json(indent=2)
    except Exception as e:
        _record_timing("fred_search", query, (time.perf_counter() - t0) * 1000, False)
        return _error_json("fred_search", e)


@tool
async def fred_get_series(series_id: str, start_date: str = "2019-01-01") -> str:
    """Fetch numerical time series data for a known FRED series ID.
    Returns the most recent 12 data points plus metadata (title, units).
    Use AFTER fred_search to get actual numbers. Common IDs: FEDMINNFRWG
    (federal min wage), UNRATE (unemployment), CPIAUCSL (CPI)."""
    t0 = time.perf_counter()
    try:
        result = await _fred_get_series(series_id=series_id, start_date=start_date)
        _record_timing("fred_get_series", series_id, (time.perf_counter() - t0) * 1000, True)
        return result.model_dump_json(indent=2)
    except Exception as e:
        _record_timing("fred_get_series", series_id, (time.perf_counter() - t0) * 1000, False)
        return _error_json("fred_get_series", e)


@tool
async def fred_get_many(series_ids: list[str], start_date: str = "2019-01-01") -> str:
    """Fetch MULTIPLE FRED series in ONE call (parallel). Much faster than
    calling fred_get_series repeatedly. Pass a list of series IDs.
    Example: fred_get_many(["UNRATE", "CPIAUCSL", "FEDMINNFRWG"])
    Returns all results plus any per-series errors."""
    t0 = time.perf_counter()
    try:
        result = await _fred_get_many(series_ids=series_ids, start_date=start_date)
        _record_timing("fred_get_many", str(series_ids), (time.perf_counter() - t0) * 1000, True)
        return result.model_dump_json(indent=2)
    except Exception as e:
        _record_timing("fred_get_many", str(series_ids), (time.perf_counter() - t0) * 1000, False)
        return _error_json("fred_get_many", e)


@tool
async def bls_get_data(series_ids: list[str], start_year: str = "2020") -> str:
    """Fetch employment, wage, and price data from the Bureau of Labor Statistics.
    More granular than FRED — industry-specific employment, state-level labor force data.
    Max 50 series per call. Series ID patterns: CEU[industry]01 for employment,
    LAUST[fips]0000000000003 for state unemployment rate."""
    t0 = time.perf_counter()
    try:
        result = await _bls_get_data(series_ids=series_ids, start_year=start_year)
        _record_timing("bls_get_data", str(series_ids), (time.perf_counter() - t0) * 1000, True)
        return result.model_dump_json(indent=2)
    except Exception as e:
        _record_timing("bls_get_data", str(series_ids), (time.perf_counter() - t0) * 1000, False)
        return _error_json("bls_get_data", e)


@tool
async def search_academic_papers(
    query: str, year_range: str = "2015-2026", limit: int = 5
) -> str:
    """Search Semantic Scholar for peer-reviewed economics research.
    Returns titles, abstracts, TLDRs, citation counts. Prioritize
    highly-cited papers and meta-analyses. Cross-reference with search_openalex."""
    t0 = time.perf_counter()
    try:
        result = await _search_academic_papers(
            query=query, year_range=year_range, limit=limit
        )
        _record_timing("search_academic_papers", query, (time.perf_counter() - t0) * 1000, True)
        return result.model_dump_json(indent=2)
    except Exception as e:
        _record_timing("search_academic_papers", query, (time.perf_counter() - t0) * 1000, False)
        return _error_json("search_academic_papers", e)


@tool
async def search_openalex(
    query: str, sort_by: str = "relevance_score", limit: int = 5
) -> str:
    """Search OpenAlex for economics academic works. IMPORTANT: Use quoted phrases
    for the core policy concept to get relevant results. Example:
    '"minimum wage" employment elasticity' NOT 'minimum wage employment elasticity'.
    '"universal basic income" poverty' NOT 'universal basic income poverty'.
    Better coverage for NBER working papers, Fed research. Provides open_access_url."""
    t0 = time.perf_counter()
    try:
        result = await _search_openalex(query=query, sort_by=sort_by, limit=limit)
        _record_timing("search_openalex", query, (time.perf_counter() - t0) * 1000, True)
        return result.model_dump_json(indent=2)
    except Exception as e:
        _record_timing("search_openalex", query, (time.perf_counter() - t0) * 1000, False)
        return _error_json("search_openalex", e)


@tool
async def search_cbo_reports(query: str, limit: int = 5) -> str:
    """Search CBO for official policy analysis reports. Returns FULL report text
    (not just snippets) via advanced search. CBO produces authoritative nonpartisan
    impact analyses, cost estimates, and employment projections. No need to
    fetch_document_text on CBO URLs — this tool returns the content directly."""
    t0 = time.perf_counter()
    try:
        result = await _search_cbo_reports(query=query, limit=limit)
        _record_timing("search_cbo_reports", query, (time.perf_counter() - t0) * 1000, True)
        return result.model_dump_json(indent=2)
    except Exception as e:
        _record_timing("search_cbo_reports", query, (time.perf_counter() - t0) * 1000, False)
        return _error_json("search_cbo_reports", e)


@tool
async def web_search_news(query: str, recency: str = "past_month") -> str:
    """Search the web for recent news, policy proposals, and real-world outcomes.
    recency options: past_week, past_month, past_year.
    Use to find current legislative status or outcomes from analogous policies."""
    t0 = time.perf_counter()
    try:
        result = await _web_search_news(query=query, recency=recency)
        _record_timing("web_search_news", query, (time.perf_counter() - t0) * 1000, True)
        return result.model_dump_json(indent=2)
    except Exception as e:
        _record_timing("web_search_news", query, (time.perf_counter() - t0) * 1000, False)
        return _error_json("web_search_news", e)


@tool
async def fetch_document_text(url: str, max_chars: int = 4000) -> str:
    """Fetch and extract readable text from a URL (HTML or PDF).
    Auto-checks Unpaywall for free versions of paywalled academic papers.
    DO NOT use on cbo.gov URLs (blocked by Cloudflare — use search_cbo_reports instead).
    DO NOT use on publisher URLs (wiley, oup, springer — use abstracts from search tools).
    GOOD for: news articles, Wikipedia, open-access repository PDFs, government sites."""
    t0 = time.perf_counter()
    try:
        result = await _fetch_document_text(url=url, max_chars=max_chars)
        _record_timing("fetch_document_text", url, (time.perf_counter() - t0) * 1000, True)
        return result.model_dump_json(indent=2)
    except Exception as e:
        _record_timing("fetch_document_text", url, (time.perf_counter() - t0) * 1000, False)
        return _error_json("fetch_document_text", e)


# ---------------------------------------------------------------------------
# Phase-to-tool mapping
# ---------------------------------------------------------------------------

PHASE_1_TOOLS = [web_search_news, fetch_document_text, fred_get_series, fred_get_many]
PHASE_2_TOOLS = [fred_search, fred_get_series, fred_get_many, bls_get_data]
PHASE_3_TOOLS = []  # No tools — pure reasoning
PHASE_4_TOOLS = [
    search_academic_papers,
    search_openalex,
    search_cbo_reports,
    fetch_document_text,
    web_search_news,
]
PHASE_5_TOOLS = []  # No tools — synthesis only
