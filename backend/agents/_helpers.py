"""Shared helpers for phase-gated ReAct agents.

Used by both the analyst agent and sector agents (housing, labor, etc.).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from backend.agents.llm import get_chat_model
from backend.agents.schemas import ToolCallRecord

logger = logging.getLogger(__name__)


def _extract_json_block(text) -> dict:
    """Extract JSON from a ```json ... ``` code fence, or parse the whole text.

    Handles non-string content (e.g., Gemini returns list of parts).
    """
    if not isinstance(text, str):
        if isinstance(text, list):
            parts = []
            for part in text:
                if isinstance(part, dict) and "text" in part:
                    parts.append(part["text"])
                elif isinstance(part, str):
                    parts.append(part)
                else:
                    parts.append(str(part))
            text = "\n".join(parts)
        else:
            text = str(text)

    # Try fenced JSON first
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        extracted = match.group(1).strip()
        if extracted:
            return _parse_json_lenient(extracted)
    # Try unfenced — find first { ... last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return _parse_json_lenient(text[start : end + 1])
    raise ValueError(f"No JSON found in LLM output: {text[:300]}...")


def _parse_json_lenient(text: str) -> dict:
    """Parse JSON with fixes for common LLM mistakes (trailing commas, etc)."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fix trailing commas before } or ]
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # Fix unquoted None/null values
    fixed = fixed.replace(": None", ": null").replace(":None", ":null")
    return json.loads(fixed)


def _extract_tool_records(messages: list, phase: int) -> list[ToolCallRecord]:
    """Walk message history and extract tool call audit records."""
    records: list[ToolCallRecord] = []
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                records.append(
                    ToolCallRecord(
                        phase=phase,
                        tool_name=tc["name"],
                        arguments=tc.get("args", {}),
                        result_summary="",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                )
        if hasattr(msg, "type") and msg.type == "tool" and records:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            records[-1].result_summary = content[:200]
    return records


def _is_json_text(text: str) -> bool:
    """Check if text looks like JSON fragment rather than natural language."""
    t = text.strip()
    if not t:
        return True
    # Obvious JSON tokens
    if t.startswith(("```", '{"', '"', "}", "{", "[", "]", "true", "false", "null")):
        return True
    # JSON key-value patterns
    if '": ' in t or '":' in t:
        return True
    # JSON line endings
    if t.endswith((",", "{", "[", '",', '"}', '"]', '"')):
        return True
    # Partial JSON values — lines that look like they continue a JSON field
    if t.startswith(("- **", "###")) and len(t) < 80:
        return True  # Markdown headers from JSON-derived content in final output
    return False


def _summarize_tool_result(tool_name: str, raw_output: str) -> str:
    """Extract a human-readable summary from raw tool output."""
    import json as _json
    try:
        data = _json.loads(raw_output)
    except (ValueError, TypeError):
        return raw_output[:80]

    if tool_name == "fred_get_series":
        title = data.get("title", "")
        val = data.get("latest_value", "")
        date = data.get("latest_date", "")
        return f"{title}: {val} ({date})" if title else raw_output[:80]

    if tool_name == "fred_get_many":
        results = data.get("results", [])
        parts = []
        for r in results[:3]:
            parts.append(f"{r.get('title', r.get('series_id', '?'))}: {r.get('latest_value', '?')}")
        if len(results) > 3:
            parts.append(f"+{len(results) - 3} more")
        return "; ".join(parts) if parts else f"{len(results)} series"

    if tool_name == "fred_search":
        results = data.get("results", [])
        return f"Found {len(results)} series" + (f" — {results[0].get('title', '')[:50]}" if results else "")

    if tool_name == "bls_get_data":
        results = data.get("results", [])
        return f"{len(results)} BLS series retrieved"

    if tool_name in ("search_academic_papers", "search_openalex"):
        results = data.get("results", [])
        if results:
            return f"{len(results)} papers — {results[0].get('title', '')[:60]}"
        return "No papers found"

    if tool_name == "search_cbo_reports":
        results = data.get("results", [])
        if results:
            return f"{len(results)} CBO reports — {results[0].get('title', '')[:60]}"
        return "No CBO reports found"

    if tool_name == "web_search_news":
        results = data.get("results", [])
        if results:
            return f"{len(results)} articles — {results[0].get('title', '')[:60]}"
        return "No articles found"

    if tool_name == "fetch_document_text":
        chars = data.get("char_count", 0)
        return f"Fetched {chars} chars from {data.get('url', '?')[:50]}"

    if tool_name == "code_execute":
        result_val = data.get("result", "")
        if result_val:
            return f"Computed: {str(result_val)[:80]}"
        return data.get("error", "executed")[:80]

    if tool_name == "census_acs_query":
        rows = data.get("rows", [])
        return f"{len(rows)} census records"

    if tool_name == "hud_data":
        return f"HUD data: {data.get('dataset', '?')}"

    return raw_output[:80]


async def _run_react_phase(
    system_prompt: str,
    user_message: str,
    tools: list,
    phase_num: int,
    state: dict,
    recursion_limit: int = 40,
    parent_config: dict | None = None,
) -> tuple[dict, list[ToolCallRecord]]:
    """Run a ReAct agent for a tool-using phase. Returns (parsed_json, tool_records).

    Streams the agent via astream_events so tool calls are visible to the
    parent graph's event stream. Dispatches 'tool_activity' custom events
    for each tool call, which propagate up through _run_subgraph to SSE.

    parent_config: RunnableConfig from the calling LangGraph node. Required
    for custom events to propagate to the parent graph's event stream.
    """
    from langchain_core.callbacks.manager import adispatch_custom_event

    llm = get_chat_model()

    logger.info(f"Phase {phase_num} prompt size: {len(system_prompt)} chars (~{len(system_prompt)//4} tokens), tools: {[t.name for t in tools]}")

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=system_prompt,
    )

    input_msg = {"messages": [HumanMessage(content=user_message)]}
    tool_records: list[ToolCallRecord] = []
    final_messages: list = []
    _reasoning_texts: list[str] = []
    _pending_reasoning: str = ""  # Reasoning before the current tool call
    _pending_tool: dict | None = None
    import time as _time

    async for event in agent.astream_events(
        input_msg,
        version="v2",
        config={"recursion_limit": recursion_limit},
    ):
        kind = event.get("event", "")
        name = event.get("name", "")
        data = event.get("data", {})

        # ----- LLM turn complete → collect reasoning from content field -----
        # Claude: content is a list of blocks: [{"type":"text","text":"..."}, {"type":"tool_use",...}]
        # GPT-4o: content is a string (usually empty on tool-calling turns)
        # We extract text blocks as the agent's reasoning.
        if kind == "on_chat_model_end":
            output = data.get("output")
            if output:
                content = getattr(output, "content", "")
                reasoning_text = ""

                if isinstance(content, list):
                    # Claude format: list of typed blocks
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif isinstance(block, str):
                            text_parts.append(block)
                    reasoning_text = " ".join(text_parts).strip()
                elif isinstance(content, str):
                    reasoning_text = content.strip()

                if reasoning_text and not _is_json_text(reasoning_text) and len(reasoning_text) > 20:
                    _reasoning_texts.append(reasoning_text[:600])
                    _pending_reasoning = reasoning_text[:300]

        # ----- Tool call starting → buffer for duration tracking -----
        elif kind == "on_tool_start":
            tool_input = data.get("input", {})
            if isinstance(tool_input, dict):
                key_arg = (tool_input.get("series_id")
                           or tool_input.get("query")
                           or tool_input.get("series_ids")
                           or tool_input.get("url")
                           or tool_input.get("code", "")[:40] or "")
                if isinstance(key_arg, list):
                    key_arg = ", ".join(str(s) for s in key_arg[:4])
                    if len(tool_input.get("series_ids", [])) > 4:
                        key_arg += f" +{len(tool_input['series_ids']) - 4} more"
                args_display = str(key_arg)[:80]
            else:
                args_display = str(tool_input)[:80]
            _pending_tool = {"name": name, "args": args_display, "t0": _time.perf_counter()}
            logger.info(f"Phase {phase_num} tool: {name}({args_display[:60]})")

        # ----- Tool call completed → emit clean event -----
        elif kind == "on_tool_end":
            output_obj = data.get("output", "")
            raw_output = getattr(output_obj, "content", None) or str(output_obj)
            result_summary = _summarize_tool_result(name, raw_output)
            duration_ms = 0
            if _pending_tool and _pending_tool["name"] == name:
                duration_ms = round((_time.perf_counter() - _pending_tool["t0"]) * 1000)
            tool_records.append(
                ToolCallRecord(
                    phase=phase_num, tool_name=name,
                    arguments=data.get("input", {}),
                    result_summary=raw_output[:200],
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    duration_ms=duration_ms,
                )
            )
            if parent_config:
                try:
                    await adispatch_custom_event("tool_complete", {
                        "tool": name,
                        "args": _pending_tool["args"] if _pending_tool else "",
                        "result": result_summary,
                        "duration_ms": duration_ms,
                        "phase": str(phase_num),
                        "reasoning": _pending_reasoning if _pending_reasoning else None,
                    }, config=parent_config)
                    logger.info(f"Phase {phase_num} dispatched tool_complete for {name}")
                except Exception as dispatch_err:
                    logger.error(f"Phase {phase_num} tool_complete dispatch FAILED: {dispatch_err}")
            else:
                logger.warning(f"Phase {phase_num} no parent_config — cannot dispatch tool_complete")
            _pending_tool = None
            _pending_reasoning = ""  # Clear after attaching to tool event

        # Capture final messages from the outermost chain completion
        elif kind == "on_chain_end" and name == "LangGraph":
            output = data.get("output", {})
            if isinstance(output, dict) and "messages" in output:
                final_messages = output["messages"]

    logger.info(f"Phase {phase_num} message count: {len(final_messages)}, tool calls: {len(tool_records)}, reasoning: {len(_reasoning_texts)}")

    if not final_messages:
        raise ValueError(f"Phase {phase_num}: ReAct agent produced no messages")

    final_msg = final_messages[-1]
    final_content = final_msg.content
    logger.info(f"Phase {phase_num} final message type: {type(final_content).__name__}")
    logger.info(f"Phase {phase_num} final content preview: {str(final_content)[:500]}")

    try:
        parsed = _extract_json_block(final_content)
        return parsed, tool_records
    except (ValueError, json.JSONDecodeError):
        pass

    # Fallback: re-prompt for structured output
    logger.warning(
        f"Phase {phase_num}: No JSON in final message. Re-prompting for structured output."
    )
    followup = await llm.ainvoke(
        final_messages
        + [
            HumanMessage(
                content="You ran out of steps. Based on all the data you've gathered above, "
                "produce your final output NOW as a JSON object inside a ```json code fence. "
                "Do NOT call any more tools."
            )
        ]
    )
    parsed = _extract_json_block(followup.content)
    return parsed, tool_records


def summarize_phase_output(phase_name: str, output_json: str) -> str:
    """Programmatically compress a phase output — no LLM call needed.

    Extracts key fields from JSON, preserving numbers and findings,
    discarding verbose structure. ~0ms vs ~7s for LLM summarization.
    """
    import json as _json

    try:
        data = _json.loads(output_json)
    except (ValueError, TypeError):
        return output_json[:2000]

    # Evidence phase gets more generous limits — study details are where
    # the analytical value lives.
    is_evidence = "evidence" in phase_name.lower()
    str_limit = 400 if is_evidence else 200
    line_cap = 80 if is_evidence else 60

    lines = [f"[{phase_name}]"]

    def _extract_evidence_item(item: dict, prefix: str) -> None:
        """Extract the fields that matter from an EvidenceItem."""
        title = item.get("title", "")
        finding = item.get("key_finding", "")
        effect = item.get("effect_size", "")
        confidence = item.get("confidence", "")
        authors = item.get("authors", "")
        year = item.get("year", "")
        applicability = item.get("applicability", "")

        header = f"{prefix}{title}"
        if authors:
            header += f" ({authors}, {year})" if year else f" ({authors})"
        elif year:
            header += f" ({year})"
        if confidence:
            header += f" [{confidence}]"
        lines.append(header)

        if finding:
            lines.append(f"{prefix}  finding: {finding[:400]}")
        if effect:
            lines.append(f"{prefix}  effect: {effect[:300]}")
        if applicability:
            lines.append(f"{prefix}  applicability: {applicability[:300]}")

    def _is_evidence_item(item: dict) -> bool:
        return "key_finding" in item or "effect_size" in item or "source_type" in item

    def _extract(obj, prefix="", depth=0):
        if depth > 3:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (int, float)):
                    lines.append(f"{prefix}{k}: {v}")
                elif isinstance(v, str) and len(v) < str_limit and v:
                    lines.append(f"{prefix}{k}: {v}")
                elif isinstance(v, list) and len(v) <= 5 and all(isinstance(x, str) for x in v):
                    lines.append(f"{prefix}{k}: {', '.join(v[:5])}")
                elif isinstance(v, (dict, list)):
                    _extract(v, prefix=f"{prefix}{k}.", depth=depth + 1)
        elif isinstance(obj, list):
            for i, item in enumerate(obj[:8]):
                if isinstance(item, dict):
                    # Evidence items get special extraction
                    if _is_evidence_item(item):
                        _extract_evidence_item(item, f"{prefix}[{i}] ")
                        continue
                    # Generic: try identifying fields
                    name = item.get("name") or item.get("metric_name") or item.get("region") or item.get("region_name") or item.get("pathway_id") or item.get("title") or ""
                    value = item.get("value") or item.get("central_estimate") or item.get("latest_value") or ""
                    extra = item.get("trend") or item.get("confidence") or item.get("relevance") or item.get("verdict") or ""
                    if name or value:
                        lines.append(f"{prefix}[{i}] {name}: {value} {extra}".strip())
                elif isinstance(item, str) and len(item) < str_limit:
                    lines.append(f"{prefix}[{i}] {item}")

    _extract(data)

    result = "\n".join(lines[:line_cap])
    logger.info(f"Summarized {phase_name}: {len(output_json)} chars → {len(result)} chars (programmatic)")
    return result


async def _run_reasoning_phase(
    system_prompt: str,
    user_message: str,
) -> dict:
    """Run a single LLM call for a no-tool phase. Returns parsed JSON dict."""
    llm = get_chat_model()

    response = await llm.ainvoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
    )

    return _extract_json_block(response.content)


async def _run_code_phase(
    system_prompt: str,
    user_message: str,
    phase_num: int,
) -> tuple[dict, list[ToolCallRecord]]:
    """Two-step LLM→code_execute→LLM pattern for deterministic math phases.

    Step 1: LLM generates Python code to compute results.
    Step 2: Execute the code in the sandbox.
    Step 3: LLM formats the execution result into the required JSON schema.

    Much faster than ReAct for phases that only need code_execute — cuts
    LLM round-trips from ~5 to 2 and removes the agent decision loop.
    """
    from backend.tools.code_execute import code_execute

    llm = get_chat_model()

    logger.info(f"Phase {phase_num} (code_phase): generating computation code")

    # Step 1: Ask LLM to produce Python code
    code_prompt = (
        f"{system_prompt}\n\n"
        "IMPORTANT: Instead of calling tools, write a SINGLE Python code block that "
        "computes ALL required values. Available: math, statistics, json, Decimal. "
        "Assign the final answer to a variable named `result` as a dict. "
        "Wrap your code in ```python ... ```."
    )

    code_response = await llm.ainvoke(
        [SystemMessage(content=code_prompt), HumanMessage(content=user_message)]
    )

    code_text = code_response.content
    if not isinstance(code_text, str):
        if isinstance(code_text, list):
            code_text = "\n".join(
                p["text"] if isinstance(p, dict) and "text" in p else str(p)
                for p in code_text
            )
        else:
            code_text = str(code_text)

    # Extract Python code from fence
    import re
    code_match = re.search(r"```(?:python)?\s*(.*?)\s*```", code_text, re.DOTALL)
    if not code_match:
        # No code block — fall back to reasoning-only (LLM produced JSON directly)
        logger.warning(f"Phase {phase_num}: LLM produced no code block, trying direct JSON parse")
        try:
            return _extract_json_block(code_text), []
        except (ValueError, json.JSONDecodeError):
            # Last resort: ask again for JSON
            followup = await llm.ainvoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
            )
            return _extract_json_block(followup.content), []

    code = code_match.group(1).strip()
    logger.info(f"Phase {phase_num}: executing {len(code)} chars of generated code")

    # Step 2: Execute
    exec_result = await code_execute(code=code)
    tool_record = ToolCallRecord(
        phase=phase_num,
        tool_name="code_execute",
        arguments={"code_length": len(code)},
        result_summary=(exec_result.result or exec_result.stdout or exec_result.error or "")[:200],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    if exec_result.error:
        logger.warning(f"Phase {phase_num}: code execution error: {exec_result.error}")
        # Feed the error back to LLM for a retry
        retry_response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
            HumanMessage(
                content=f"Your code had an error: {exec_result.error}\n"
                "Fix the code and try again. Wrap in ```python ... ```."
            ),
        ])
        retry_text = retry_response.content if isinstance(retry_response.content, str) else str(retry_response.content)
        retry_match = re.search(r"```(?:python)?\s*(.*?)\s*```", retry_text, re.DOTALL)
        if retry_match:
            exec_result = await code_execute(code=retry_match.group(1).strip())
            tool_record.result_summary = (exec_result.result or exec_result.error or "")[:200]

    # Step 3: LLM formats execution output into the required JSON schema
    computation_data = exec_result.result or exec_result.stdout or "{}"
    format_response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=(
            f"Here are the computed results:\n```\n{computation_data}\n```\n\n"
            "Format these into the required JSON schema. "
            "Do NOT recompute — use the numbers above exactly. "
            "Produce JSON in ```json ... ``` code fence."
        )),
    ])

    parsed = _extract_json_block(format_response.content)
    logger.info(f"Phase {phase_num} (code_phase) complete: 2 LLM calls + 1 code_execute")

    return parsed, [tool_record]
