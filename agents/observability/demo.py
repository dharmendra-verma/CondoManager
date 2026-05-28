"""Runnable demo: FastAPI + httpx + openai call, all traced.

Jira: CM-21  | Epic: Observability  | Phase 0

Run::

    OTEL_TRACES_EXPORTER=console python -m agents.observability.demo

Then in another terminal::

    curl http://localhost:8000/echo/hello

The first terminal prints OTel spans for the FastAPI server handler, the
outbound httpx call, and the openai call. Useful as a sanity check before
wiring App Insights (CM-22).

This file is intentionally kept out of the test suite — it spins up a
real HTTP server. The instrumentation tests under
``tests/observability/test_instrumentation.py`` cover the same code paths
with ``InMemorySpanExporter`` + ``respx`` mocks.
"""

from __future__ import annotations

import asyncio
import os

import httpx
from fastapi import FastAPI
from openai import AsyncOpenAI

from . import configure_otel, with_request_id

app = FastAPI(title="condomanager-observability-demo")


@app.on_event("startup")
async def _startup() -> None:
    # `service_name` is what shows up in App Insights / LangSmith / etc.
    configure_otel(service_name="observability-demo", environment="dev", app=app)


@app.get("/echo/{msg}")
async def echo(msg: str) -> dict[str, str]:
    """Demo handler — exercises every instrumented surface in one request."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        # Outbound HTTP — produces an httpx client span.
        r = await client.get("https://httpbin.org/json")
        upstream_status = r.status_code

    # Outbound OpenAI — produces an openinference LLM span (or just import-
    # exercise if no real key is present). With `sk-fake` the call will fail
    # at the HTTP layer; demo doesn't actually need the LLM to succeed.
    client_openai = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", "sk-demo-fake"))
    try:
        await client_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": msg}],
            max_tokens=1,
            timeout=2.0,
        )
        openai_ok = True
    except Exception:
        # Demo is about tracing the call, not about a successful LLM response.
        openai_ok = False

    with with_request_id() as rid:
        return {
            "request_id": rid,
            "msg": msg,
            "upstream_status": str(upstream_status),
            "openai_ok": str(openai_ok),
        }


def main() -> None:
    import uvicorn  # noqa: PLC0415  -- demo-only dep, not in pyproject

    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    asyncio.run(asyncio.sleep(0))  # ensure event loop is sane on Windows
    main()
