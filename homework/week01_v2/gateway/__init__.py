"""Mini LLM Gateway v2 — unified model-calling service (dual protocol).

Gateway-internal five layers:
  1. interface   FastAPI entry, Pydantic protocols, unified error envelope
  2. governance  model whitelist/routing, prompt-template registry, rate limiting
  3. execution   dual-protocol adapters, retry/fallback, streaming forwarding
  4. egress      structured-output double validation with bounded repair
  5. observability  CallTrace (token classification + latency + TTFT) and /trace

Only the gateway is implemented here; the calling side (Agent Harness Loop/Run)
lives elsewhere.
"""

__version__ = "2.0.0"
