"""OpenTelemetry setup.

Every request becomes one TRACE; each step inside it (guardrail, retrieval,
LLM call) becomes a SPAN. Spans always go to traces/traces.jsonl so you can
inspect them offline. If OTEL_EXPORTER_OTLP_ENDPOINT is set, spans are ALSO
sent over OTLP to any backend (Langfuse, Jaeger, Grafana Tempo, ...).
"""
import json
import os
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

_TRACER = None


class JsonlFileExporter(SpanExporter):
    """Writes each finished span as one JSON line - easy to read and grep."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans):
        with self.path.open("a", encoding="utf-8") as f:
            for s in spans:
                f.write(json.dumps({
                    "trace_id": format(s.context.trace_id, "032x"),
                    "span_id": format(s.context.span_id, "016x"),
                    "parent_id": format(s.parent.span_id, "016x") if s.parent else None,
                    "name": s.name,
                    "duration_ms": round((s.end_time - s.start_time) / 1e6, 3),
                    "status": s.status.status_code.name,
                    "attributes": {k: (list(v) if isinstance(v, tuple) else v)
                                   for k, v in (s.attributes or {}).items()},
                }) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


def get_tracer(service_name: str = "rag-eval-demo"):
    global _TRACER
    if _TRACER is not None:
        return _TRACER

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    trace_file = os.getenv("TRACE_FILE", "traces/traces.jsonl")
    provider.add_span_processor(SimpleSpanProcessor(JsonlFileExporter(trace_file)))

    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        # Reads endpoint + headers from the standard OTEL_* env vars.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer(service_name)
    return _TRACER


def flush():
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()
