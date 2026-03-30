from __future__ import annotations

import asyncio

from backend.tools.fred_get_series import fred_get_series
from backend.tools.schemas import FredBatchOutput, FredSeriesOutput


async def fred_get_many(
    series_ids: list[str], start_date: str = "2019-01-01"
) -> FredBatchOutput:
    """Fetch multiple FRED series in parallel.

    Returns all successful results plus a dict of series_id → error for
    any that failed. Much faster than calling fred_get_series in a loop
    because requests run concurrently over the shared HTTP client.
    """

    async def _safe_fetch(sid: str) -> tuple[str, FredSeriesOutput | None, str | None]:
        try:
            result = await fred_get_series(series_id=sid, start_date=start_date)
            return sid, result, None
        except Exception as e:
            return sid, None, f"{type(e).__name__}: {e}"

    tasks = [_safe_fetch(sid) for sid in series_ids]
    outcomes = await asyncio.gather(*tasks)

    results = []
    errors = {}
    for sid, result, error in outcomes:
        if result is not None:
            results.append(result)
        elif error:
            errors[sid] = error

    return FredBatchOutput(results=results, errors=errors)
