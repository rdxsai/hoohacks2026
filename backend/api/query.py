"""
Query endpoint — non-streaming entry point for the pipeline.

POST /api/analyze runs the full LangGraph pipeline and returns the
synthesis report when complete.
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter

from backend.models.api import QueryRequest

router = APIRouter()


@router.post("/analyze", response_model=None)
async def analyze_policy(request: QueryRequest) -> dict:
    """Run the full PolicyPulse analysis pipeline (non-streaming)."""
    from backend.pipeline.graph import build_pipeline_graph

    graph = build_pipeline_graph()

    initial = {
        "query": request.query,
        "user_context": request.context.model_dump(exclude_none=True),
        "session_id": str(uuid.uuid4()),
        "start_time": time.time(),
        "sector_reports": [],
        "stage_times": {},
    }

    final = await graph.ainvoke(initial)

    if final.get("synthesis_report"):
        report = final["synthesis_report"]
        if hasattr(report, "model_dump"):
            return report.model_dump()
        return report

    return {
        "error": "Pipeline did not produce a synthesis report",
        "session_id": initial["session_id"],
        "stage_times": final.get("stage_times", {}),
    }


@router.get("/health/pipeline")
async def pipeline_health() -> dict:
    """Check that the pipeline graph is importable."""
    checks = {}
    try:
        from backend.pipeline.graph import build_pipeline_graph
        g = build_pipeline_graph()
        checks["graph"] = "ok"
        checks["nodes"] = len(g.get_graph().nodes)
    except Exception as e:
        checks["graph"] = str(e)

    return {"status": "ok" if checks.get("graph") == "ok" else "degraded", "modules": checks}
