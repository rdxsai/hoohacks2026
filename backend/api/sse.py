"""
SSE streaming endpoint — streams LangGraph pipeline events to the frontend.

The frontend connects to GET /stream?query=... and receives real-time events
as each pipeline stage executes. Uses graph.astream_events() for native
LangGraph event streaming — no manual emit callbacks or asyncio queues.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

from backend.api.event_translator import EventTranslator

router = APIRouter()


async def _event_stream(
    query: str,
    user_context: dict[str, Any],
) -> AsyncGenerator[str, None]:
    """Stream LangGraph pipeline events as SSE."""
    from backend.pipeline.graph import build_pipeline_graph

    graph = build_pipeline_graph()

    initial = {
        "query": query,
        "user_context": user_context,
        "session_id": str(uuid.uuid4()),
        "start_time": time.time(),
        "sector_reports": [],
        "stage_times": {},
    }

    translator = EventTranslator()

    try:
        async for event in graph.astream_events(initial, version="v2"):
            translated = translator.translate(event)
            if translated is not None:
                data = json.dumps(translated, default=str)
                yield f"data: {data}\n\n"

        # Pipeline complete
        total = time.time() - initial["start_time"]
        complete_event = {
            "type": "pipeline_complete",
            "data": {
                "session_id": initial["session_id"],
                "total_seconds": round(total, 1),
            },
            "timestamp": time.time(),
        }
        yield f"data: {json.dumps(complete_event, default=str)}\n\n"

    except Exception as e:
        error_event = {
            "type": "pipeline_error",
            "data": {"error": str(e)},
            "timestamp": time.time(),
        }
        yield f"data: {json.dumps(error_event, default=str)}\n\n"


@router.get("/stream")
async def stream_pipeline(
    query: str = Query(..., min_length=10, description="The policy question"),
    role: str = Query(None, description="User role"),
    graduation_year: int = Query(None, description="Graduation year"),
    field: str = Query(None, description="Field of study/work"),
    location: str = Query(None, description="User location"),
    concern: str = Query(None, description="Primary concern"),
) -> StreamingResponse:
    """Stream the full analysis pipeline as Server-Sent Events."""
    user_context = {
        k: v for k, v in {
            "role": role,
            "graduation_year": graduation_year,
            "field": field,
            "location": location,
            "concern": concern,
        }.items() if v is not None
    }

    return StreamingResponse(
        _event_stream(query=query, user_context=user_context),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
