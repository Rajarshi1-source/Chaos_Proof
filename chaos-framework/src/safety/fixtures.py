"""Chaos fixtures — in-app fault triggers, armed by the runner (§C.5).

Every experiment carries a `chaos_fixture` field. Until now nothing read it:
the field was declarative and inert, which is the worst state for a safety-
adjacent knob to be in — it looks like it is doing something. Surfaced while
building the counterfactual pairs in Phase 10, where an in-app error rate was
the only instrument precise enough to answer the question being asked.

WHY AN IN-APP FIXTURE AT ALL, when Litmus can inject network faults: because
packet loss is the wrong instrument for measuring what a fallback is worth. A
counterfactual needs a fault severity where the "without" arm degrades but
survives its own abort threshold — roughly 10% of REQUESTS failing. At 10%
packet loss TCP retransmits recover essentially everything (measured: both arms
showed ~0 failed requests), and the loss rate that does produce request
failures is nonlinear and bistable near the abort boundary (also measured: the
same 10% loss aborted three of five `without` arms while two sailed through).
An explicit error rate is controllable; packet loss is not.

ARMING IS THE EASY HALF. Disarming is the half that matters, so every arm()
returns a disarm callable that the CALLER MUST REGISTER WITH THE CLEANUP SAGA
before anything else can raise. A fixture left armed is a service quietly
failing a tenth of its requests with no chaos experiment running to explain it.
"""

import json
import urllib.error
import urllib.request

from . import flags as flags_mod

# Fixtures that take a percentage and stay armed until told otherwise. These are
# the dangerous ones: a fixture that expires on its own cannot be left behind.
STATEFUL = {"flaky-payments"}

# The app's HTTP port. NOT a single default: order-api is 8080 and
# payment-service is 8081, and assuming 8080 for everything produced a
# connection-refused that looked exactly like a dead pod — the port-forward
# came up fine (kubectl was listening), so the readiness probe in `_forward`
# passed and only the eventual request failed. Ten counterfactual arms were
# lost to it. Sourced from charts/target-app/values.yaml.
PORTS = {"order-api": 8080, "payment-service": 8081, "inventory-service": 8082}


def port_for(app: str) -> int:
    if app not in PORTS:
        raise RuntimeError(
            f"no HTTP port known for {app!r}. Add it to PORTS rather than "
            "letting it fall back to a default — a wrong port here fails as "
            "connection-refused, which reads as a dead pod.")
    return PORTS[app]


def _post(base: str, path: str, timeout: float = 10.0) -> str:
    request = urllib.request.Request(f"{base}{path}", method="POST", data=b"")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def arm(namespace: str, app: str, fixture: str, percent: int = 0,
        port: int | None = None) -> tuple[str, callable]:
    """Arm a fixture on EVERY replica; return (summary, disarm).

    Every replica, not one: a fixture armed on half the pods makes the observed
    error rate depend on which pod the load balancer picked, which is an
    unattributable mixture rather than a controlled fault.
    """
    port = port_for(app) if port is None else port

    if fixture not in STATEFUL:
        # cpu-burner and hungry-worker are self-limiting: they expire, or they
        # OOM the container and the pod restarts clean. Nothing to disarm.
        return f"{fixture} is self-limiting; not armed by the runner", lambda: "n/a"

    pods = flags_mod._pods(namespace, app)
    if not pods:
        raise RuntimeError(f"no pods for {app} in {namespace}; cannot arm {fixture}")

    # Wrapped in the SAME retry the flag plane uses. A port-forward to a pod
    # that is still rolling, or one whose forward is torn down as the pod is
    # replaced, fails with a bare connection error — and the first version of
    # this module let that abort the whole pair. Ten arms were lost to a
    # transient socket.
    def _post_all(pct: int) -> list[str]:
        results = []
        for pod in flags_mod._pods(namespace, app):
            def call(pod=pod):
                with flags_mod._forward(namespace, pod, port) as base:
                    return _post(base, f"/fixtures/flaky?percent={pct}")
            results.append(flags_mod._with_retry(f"arm {fixture} on {pod}", call))
        return results

    armed = _post_all(percent)

    def disarm() -> str:
        results = _post_all(0)
        # Read back rather than trust the write. This step failing silently is
        # the worst outcome available here.
        return f"disarmed {fixture} on {len(results)} pod(s)"

    return f"armed {fixture} at {percent}% on {len(armed)} pod(s)", disarm
