"""Enumerate every span / metric / log-event attribute Claude Code emitted.

Reads the two captured OTLP JSON dumps (`test-a-default.jsonl`,
`test-b-content-on.jsonl`) and prints a structured inventory of:

- every span name and the set of attribute keys observed
- every metric name and the set of attribute keys observed
- every log-event name and the set of attribute keys observed

Used to populate the per-attribute emission table in
`emission-coverage.md`.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent


def _attr_dict(attrs: list[dict]) -> dict[str, object]:
    out: dict[str, object] = {}
    for attr in attrs:
        key = attr.get("key")
        v = attr.get("value", {})
        # OTLP/JSON values are {"stringValue": "..."}, {"intValue": "..."},
        # {"doubleValue": ...}, {"boolValue": ...}, {"arrayValue": {...}}, etc.
        if "stringValue" in v:
            out[key] = v["stringValue"]
        elif "intValue" in v:
            out[key] = int(v["intValue"])
        elif "doubleValue" in v:
            out[key] = v["doubleValue"]
        elif "boolValue" in v:
            out[key] = v["boolValue"]
        elif "arrayValue" in v:
            out[key] = [list(x.keys()) for x in v["arrayValue"].get("values", [])]
        else:
            out[key] = v
    return out


def _walk_traces(body: dict, spans: dict[str, set[str]], span_events: dict[str, set[str]], samples: dict[str, dict]) -> None:
    for rs in body.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                name = span.get("name", "<unnamed>")
                attrs = _attr_dict(span.get("attributes", []))
                spans[name].update(attrs.keys())
                samples.setdefault(("span", name), attrs)
                for ev in span.get("events", []):
                    ev_name = ev.get("name", "<unnamed>")
                    ev_attrs = _attr_dict(ev.get("attributes", []))
                    span_events[f"{name}::{ev_name}"].update(ev_attrs.keys())
                    samples.setdefault(("span_event", f"{name}::{ev_name}"), ev_attrs)


def _walk_metrics(body: dict, metrics: dict[str, set[str]], samples: dict[str, dict]) -> None:
    for rm in body.get("resourceMetrics", []):
        for sm in rm.get("scopeMetrics", []):
            for metric in sm.get("metrics", []):
                name = metric.get("name", "<unnamed>")
                # Both sum and gauge live in a payload dict; both expose data points
                # with their own attribute lists.
                for key in ("sum", "gauge", "histogram", "exponentialHistogram", "summary"):
                    payload = metric.get(key)
                    if not payload:
                        continue
                    for dp in payload.get("dataPoints", []):
                        attrs = _attr_dict(dp.get("attributes", []))
                        metrics[name].update(attrs.keys())
                        samples.setdefault(("metric", name), attrs)


def _walk_logs(body: dict, log_events: dict[str, set[str]], samples: dict[str, dict]) -> None:
    for rl in body.get("resourceLogs", []):
        for sl in rl.get("scopeLogs", []):
            for rec in sl.get("logRecords", []):
                attrs = _attr_dict(rec.get("attributes", []))
                ev_name = attrs.get("event.name") or rec.get("eventName") or "<unnamed-log>"
                ev_body = rec.get("body", {})
                if isinstance(ev_body, dict) and "kvlistValue" in ev_body:
                    body_attrs = _attr_dict(ev_body["kvlistValue"].get("values", []))
                    attrs.update({f"body.{k}": v for k, v in body_attrs.items()})
                log_events[ev_name].update(attrs.keys())
                samples.setdefault(("log_event", ev_name), attrs)


def main(path: Path) -> dict:
    spans: dict[str, set[str]] = defaultdict(set)
    span_events: dict[str, set[str]] = defaultdict(set)
    metrics: dict[str, set[str]] = defaultdict(set)
    log_events: dict[str, set[str]] = defaultdict(set)
    samples: dict[tuple[str, str], dict] = {}

    with path.open() as handle:
        for line in handle:
            env = json.loads(line)
            body = env.get("body")
            if not isinstance(body, dict):
                continue
            signal = env.get("signal", "")
            if signal == "v1/traces":
                _walk_traces(body, spans, span_events, samples)
            elif signal == "v1/metrics":
                _walk_metrics(body, metrics, samples)
            elif signal == "v1/logs":
                _walk_logs(body, log_events, samples)

    return {
        "spans": {n: sorted(a) for n, a in spans.items()},
        "span_events": {n: sorted(a) for n, a in span_events.items()},
        "metrics": {n: sorted(a) for n, a in metrics.items()},
        "log_events": {n: sorted(a) for n, a in log_events.items()},
        "samples": {f"{k[0]}|{k[1]}": v for k, v in samples.items()},
    }


if __name__ == "__main__":
    out_a = main(HERE / "test-a-default.jsonl")
    out_b = main(HERE / "test-b-content-on.jsonl")
    inventory = {"A_default_content_off": out_a, "B_content_on": out_b}
    out_path = HERE / "inventory.json"
    out_path.write_text(json.dumps(inventory, indent=2, sort_keys=True))
    print(f"wrote {out_path}")
    print("--- summary ---")
    for label, dat in (("A_default", out_a), ("B_content_on", out_b)):
        print(f"\n[{label}]")
        print(f"  span names ({len(dat['spans'])}): {sorted(dat['spans'])}")
        print(f"  span-event names ({len(dat['span_events'])}): {sorted(dat['span_events'])}")
        print(f"  metric names ({len(dat['metrics'])}): {sorted(dat['metrics'])}")
        print(f"  log-event names ({len(dat['log_events'])}): {sorted(dat['log_events'])}")
