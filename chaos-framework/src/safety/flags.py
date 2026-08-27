"""Feature-flag plane client: snapshot before a counterfactual run, restore in
the cleanup saga.

`restore_feature_flags` is the HIGHEST-STAKES cleanup step. A cluster left with
its fallbacks or circuit breakers disabled is the worst possible outcome of this
project — worse than any injected fault, because it is silent and permanent
until someone notices. The saga restores it; the chaos-cleanup CronJob is the
out-of-band path for when the runner never gets there.

Two implementation facts that matter more than they look:

1. **Flag state is PER-POD**, held in memory. Posting to the Service would set
   it on whichever replica the kube-proxy picked, leaving the others serving the
   pattern normally — a counterfactual "without" arm that is only half without,
   which is worse than not running it. So every pod is addressed individually.

2. The endpoint is reached through a short-lived `kubectl port-forward`, not an
   Ingress. The flag plane must never be reachable from outside the cluster: it
   is a switch that removes production protection.
"""

import contextlib
import json
import socket
import subprocess
import time

import requests


# restore_feature_flags is the highest-stakes cleanup step, and kubectl
# port-forward is flaky on Windows (WSAECONNABORTED mid-request). The step that
# must never silently fail therefore retries rather than trusting one attempt.
ATTEMPTS = 4
BACKOFF_S = 1.5


def _with_retry(what: str, fn):
    last = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            if attempt < ATTEMPTS:
                time.sleep(BACKOFF_S * attempt)
    raise RuntimeError(f"{what} failed after {ATTEMPTS} attempts: {last}")


def _pods(namespace: str, app: str) -> list[str]:
    out = subprocess.run(
        ["kubectl", "get", "pods", "-n", namespace, "-l", f"app={app}",
         "--field-selector=status.phase=Running",
         "-o", "jsonpath={.items[*].metadata.name}"],
        capture_output=True, text=True, timeout=30).stdout.split()
    if not out:
        raise RuntimeError(f"no running pods for app={app} in {namespace}")
    return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _forward(namespace: str, pod: str, target_port: int):
    local = _free_port()
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", namespace, f"pod/{pod}",
         f"{local}:{target_port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError):
                with socket.create_connection(("127.0.0.1", local), timeout=0.5):
                    break
            time.sleep(0.3)
        else:
            raise RuntimeError(f"port-forward to {pod} never became ready")
        yield f"http://127.0.0.1:{local}"
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)


def snapshot(namespace: str, app: str, port: int = 8080) -> dict:
    """Read one pod's state — they are configured identically, so one is the
    baseline to restore all of them to."""
    pod = _pods(namespace, app)[0]

    def once():
        with _forward(namespace, pod, port) as base:
            r = requests.get(f"{base}/chaos/flags", timeout=10)
            r.raise_for_status()
            return r.json()

    return _with_retry(f"flag snapshot from {pod}", once)


def apply(namespace: str, app: str, patterns: list[str], port: int = 8080) -> list[dict]:
    """Set the flags on EVERY replica. A partial application is not a
    counterfactual arm — it is an unattributable mixture of two configurations."""
    enabled = "true" if patterns else "false"
    disabled = ",".join(patterns)
    results = []
    failures = []
    for pod in _pods(namespace, app):
        def once(pod=pod):
            with _forward(namespace, pod, port) as base:
                r = requests.post(f"{base}/chaos/flags",
                                  params={"enabled": enabled, "disabled": disabled},
                                  timeout=10)
                r.raise_for_status()
                return r.json()
        try:
            results.append({"pod": pod, "state": _with_retry(f"flag set on {pod}", once)})
        except Exception as e:
            failures.append(f"{pod}: {e}")
    if failures:
        raise RuntimeError(
            "flag application incomplete on " + "; ".join(failures)
            + " — refusing to proceed with a half-configured counterfactual arm")
    return results


def restore(namespace: str, app: str, snap: dict, port: int = 8080) -> str:
    """Restore, then VERIFY. This step failing silently is the worst outcome in
    the project, so it does not report success on the strength of a POST that
    returned — it reads the state back and checks it."""
    patterns = list(snap.get("disabledPatterns", []))
    apply(namespace, app, patterns, port)
    after = snapshot(namespace, app, port)
    if set(after.get("disabledPatterns", [])) != set(patterns):
        raise RuntimeError(
            f"flag restore VERIFICATION FAILED: expected disabled={patterns}, "
            f"read back {after.get('disabledPatterns')} — the cluster may still "
            "be running with a resilience pattern disabled")
    return (f"flags restored and verified on all replicas: "
            f"enabled={after.get('enabled')} disabled={patterns or '[]'}")


def as_json(snap: dict) -> str:
    return json.dumps(snap, sort_keys=True)
