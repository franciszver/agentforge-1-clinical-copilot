"""Alert-threshold evaluation for the P4.5 dashboard (P4.6).

Pure threshold logic over ``app.dashboard_metrics.DashboardMetrics`` -- no
I/O, no recomputation of metrics that ``DashboardMetrics`` already has. Four
alerts, each read straight off the DTO or derived from fields already on it:

- **p95 latency**: ``metrics.p95_latency_ms`` directly.
- **error rate**: ``metrics.error_rate`` directly.
- **tool-failure rate**: DERIVED as ``retry_count / tool_call_count``. The
  DTO has no failure-rate field, only the raw counts -- see
  ``dashboard_metrics.py``'s ``retry_count`` docstring: a FAILED tool span
  IS the retry signal, so this is "tool calls that did not succeed on their
  recorded attempt, as a share of all tool calls". ``None`` (no alert) when
  ``tool_call_count == 0``. NOTE: in production today ``tool_call_count`` is
  ALWAYS 0 -- ``app.chat`` does not yet emit per-tool spans live (see
  ``app.trace_store.TraceStore.record_tool_span``'s call sites, currently
  none in ``app.chat``; tracked by #149). This alert is exercised here
  against seeded/synthetic metrics and will start evaluating real data once
  #149 lands.
- **verification-fail rate**: DERIVED as ``1 - verification_pass_rate``.
  ``None`` (no alert) when ``verification_pass_rate`` is ``None``.

**Boundary semantics: strictly greater-than.** An alert fires when
``current_value > threshold``, NOT ``>=``. A metric sitting exactly ON the
threshold reads as "at the edge, not yet over it" -- consistent with the
thresholds being phrased as ceilings ("p95 > 30s"), and it keeps a metric
seeded at a clean round number (e.g. exactly 10.0% error rate) from reading
as already-alerting.

**None handling: absence is not evidence of a problem.** A ``None`` metric
(empty store, or tool-failure rate when ``tool_call_count == 0``) never
fires an alert -- there is nothing to alert ON. This mirrors the dashboard's
own "N/A" rendering: no data is not the same as good data.

**Derived rates are rounded to 9 decimal places** before comparison. Both
derived rates involve a floating-point subtraction/division
(``1 - verification_pass_rate``, ``retry_count / tool_call_count``) that can
land a hair off an exact decimal boundary purely from binary float
representation (e.g. ``1.0 - 0.7 == 0.30000000000000004``, not ``0.3``).
9 decimal places is far finer than any rate derived from realistic span
counts needs, so this only removes float dust -- it never masks a real
difference.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.dashboard_metrics import DashboardMetrics

# Demo-tier defaults, not tuned against production traffic (there is none
# yet). Rationale for each ceiling:
#
# - p95 latency > 30_000ms (30s): the plan's Pi-tier "slow" regime for a
#   CPU-bound local Ollama model -- a single occasionally-slow request is
#   expected, but a p95 this high means most requests are crossing it.
# - error rate > 10%: high enough that a handful of one-off failures in a
#   small demo sample won't trip it, low enough to catch a systemic issue
#   (auth, connectivity, a bad deploy) before it looks "normal".
# - tool-failure rate > 20%: one in five tool calls failing points at a
#   specific broken integration (a flaky OpenEMR endpoint, bad args) rather
#   than incidental noise.
# - verification-fail rate > 30%: verification strips/blocks unverifiable
#   claims by design (P3.7) so some non-zero fail rate is normal; 30% is
#   the point where it looks more like a grounding/citation problem than
#   the system doing its job.
@dataclass(frozen=True)
class AlertThresholds:
    p95_latency_ms: float = 30_000.0
    error_rate: float = 0.10
    tool_failure_rate: float = 0.20
    verification_fail_rate: float = 0.30


DEFAULT_THRESHOLDS = AlertThresholds()


@dataclass(frozen=True)
class Alert:
    """One active alert. ``explanation`` is a hardcoded, non-PHI paragraph --
    never built from request data -- safe to render verbatim."""

    metric: str
    current_value: float
    threshold: float
    explanation: str


_P95_LATENCY_EXPLANATION = (
    "p95 response latency has crossed the alerting threshold: at least 1 in 20 "
    "clinician requests are taking longer than expected. Check the local Ollama "
    "model for load or queueing, whether a long conversation history is "
    "inflating generation time, and whether the host is under competing CPU or "
    "memory load. Sustained high p95 erodes clinician trust even when most "
    "requests are fast."
)

_ERROR_RATE_EXPLANATION = (
    "The overall request error rate has crossed the alerting threshold. Check "
    "the OpenEMR API connection for auth or connectivity failures, recent "
    "changes to the request pipeline, and the PHP error log alongside the "
    "agent's own logs. Check /health and /ready before relying on the "
    "assistant for a live session."
)

_TOOL_FAILURE_RATE_EXPLANATION = (
    "The share of tool calls ending in failure has crossed the alerting "
    "threshold. Check whether one specific tool (labs, medications, "
    "encounters, etc.) is failing against a flaky or unreachable OpenEMR "
    "endpoint, whether timeouts are tuned correctly, and whether the planner "
    "is retrying a call that will never succeed instead of surfacing a clear "
    "error to the clinician."
)

_VERIFICATION_FAIL_RATE_EXPLANATION = (
    "The verification-fail rate has crossed the alerting threshold. Some "
    "stripped or blocked claims are expected by design -- verification exists "
    "to catch unsupported statements -- but a rate this high suggests the "
    "model is frequently generating claims it cannot ground in the retrieved "
    "records. Check recent prompt or model changes and look at a sample of "
    "blocked/partially-verified responses for a pattern."
)


def evaluate_alerts(
    metrics: DashboardMetrics, thresholds: AlertThresholds = DEFAULT_THRESHOLDS
) -> list[Alert]:
    """Pure function: which of the four alerts are active for ``metrics``.

    Fixed evaluation order: p95 latency, error rate, tool-failure rate,
    verification-fail rate. See module docstring for boundary (``>``, not
    ``>=``) and ``None`` handling.
    """
    alerts: list[Alert] = []

    if metrics.p95_latency_ms is not None and metrics.p95_latency_ms > thresholds.p95_latency_ms:
        alerts.append(
            Alert(
                metric="p95 latency",
                current_value=metrics.p95_latency_ms,
                threshold=thresholds.p95_latency_ms,
                explanation=_P95_LATENCY_EXPLANATION,
            )
        )

    if metrics.error_rate is not None and metrics.error_rate > thresholds.error_rate:
        alerts.append(
            Alert(
                metric="error rate",
                current_value=metrics.error_rate,
                threshold=thresholds.error_rate,
                explanation=_ERROR_RATE_EXPLANATION,
            )
        )

    tool_failure_rate = (
        round(metrics.retry_count / metrics.tool_call_count, 9) if metrics.tool_call_count > 0 else None
    )
    if tool_failure_rate is not None and tool_failure_rate > thresholds.tool_failure_rate:
        alerts.append(
            Alert(
                metric="tool-failure rate",
                current_value=tool_failure_rate,
                threshold=thresholds.tool_failure_rate,
                explanation=_TOOL_FAILURE_RATE_EXPLANATION,
            )
        )

    verification_fail_rate = (
        round(1 - metrics.verification_pass_rate, 9) if metrics.verification_pass_rate is not None else None
    )
    if verification_fail_rate is not None and verification_fail_rate > thresholds.verification_fail_rate:
        alerts.append(
            Alert(
                metric="verification-fail rate",
                current_value=verification_fail_rate,
                threshold=thresholds.verification_fail_rate,
                explanation=_VERIFICATION_FAIL_RATE_EXPLANATION,
            )
        )

    return alerts
