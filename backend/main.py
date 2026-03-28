"""
PolicyPulse — FastAPI entrypoint.

===========================================================================
INTEGRATION GUIDE
===========================================================================

STATUS: 🟢 LIVE — Full pipeline wired up with SSE streaming.

ENDPOINTS:
  GET  /health             — Health check (Docker, frontend)
  GET  /api/health/pipeline — Check all pipeline modules are importable
  GET  /stream?query=...   — SSE stream of the full analysis pipeline
  POST /api/analyze         — Non-streaming: run pipeline, return full report

SSE STREAM (primary frontend integration):
  Samank's frontend connects to GET /stream?query=<policy question>
  Events stream in real time as each stage completes.
  See backend/api/sse.py for event types and format.

NON-STREAMING:
  POST /api/analyze with JSON body: {"query": "...", "context": {...}}
  Returns the full SynthesisReport when pipeline completes.
===========================================================================
"""

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.api.sse import router as sse_router
from backend.api.query import router as query_router

logger = logging.getLogger("policypulse")

app = FastAPI(title="PolicyPulse", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routes ---
app.include_router(sse_router)             # GET /stream
app.include_router(query_router, prefix="/api")  # POST /api/analyze, GET /api/health/pipeline


@app.get("/health")
async def health():
    """Health check — used by Docker healthchecks and frontend."""
    return {"status": "ok"}


@app.on_event("startup")
async def prewarm_llm():
    """Pre-warm LLM connection on startup so the first real request doesn't stall.

    Sends a trivial completion request to establish the HTTP connection pool.
    Runs in the background so it doesn't block server startup.
    """

    async def _warm():
        try:
            provider = getattr(settings, "llm_provider", "openai")
            if provider == "openai" and settings.openai_api_key:
                import openai

                client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
                await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                )
                logger.info("LLM pre-warm complete (OpenAI)")
            elif provider in ("google", "gemini") and (settings.google_api_key or settings.gemini_api_key):
                # Gemini — send a trivial request to warm the connection
                import httpx
                async with httpx.AsyncClient(timeout=30) as client:
                    api_key = settings.gemini_api_key or settings.google_api_key
                    model = settings.classifier_model_name
                    await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        params={"key": api_key},
                        json={"contents": [{"parts": [{"text": "ping"}]}],
                              "generationConfig": {"maxOutputTokens": 1}},
                    )
                logger.info("LLM pre-warm complete (Gemini)")
            elif provider == "anthropic" and settings.anthropic_api_key:
                import anthropic

                client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
                await client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1,
                    messages=[{"role": "user", "content": "ping"}],
                )
                logger.info("LLM pre-warm complete (Anthropic)")
            else:
                logger.info("LLM pre-warm skipped — no API key configured")
        except Exception as e:
            logger.warning(f"LLM pre-warm failed (non-fatal): {e}")

    asyncio.create_task(_warm())
