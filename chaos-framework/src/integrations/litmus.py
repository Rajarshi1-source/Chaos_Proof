"""LitmusChaos integration: applies a generated ChaosEngine and watches its
lifecycle via kubectl (the framework's kubeconfig context). Deletion is part of
cleanup and runs on every exit path (safety-plane: the saga arrives in Phase 5;
Phase 2 already refuses to leave an engine behind)."""

import json
import subprocess
import time


ENGINE_TEMPLATE = """\
apiVersion: litmuschaos.io/v1alpha1
kind: ChaosEngine
metadata:
  name: {name}
  namespace: {namespace}
spec:
  appinfo:
    appns: {namespace}
    applabel: "app={app}"
    appkind: deployment
  engineState: active
  chaosServiceAccount: pod-delete-sa
  jobCleanUpPolicy: delete
  annotationCheck: "false"
  experiments:
    - name: {fault}
      spec:
        components:
          env:
            - name: TOTAL_CHAOS_DURATION
              value: "{duration}"
            - name: CHAOS_INTERVAL
              value: "10"
            - name: FORCE
              value: "false"
            - name: PODS_AFFECTED_PERC
              value: "50"
"""


def _kubectl(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", *args],
        input=input_text, capture_output=True, text=True, timeout=60,
    )


class LitmusClient:

    def apply_pod_delete(self, namespace: str, app: str, duration_s: int) -> str:
        name = f"pod-kill-{int(time.time())}"
        manifest = ENGINE_TEMPLATE.format(
            name=name, namespace=namespace, app=app,
            fault="pod-delete", duration=duration_s,
        )
        proc = _kubectl("apply", "-f", "-", input_text=manifest)
        if proc.returncode != 0:
            raise RuntimeError(f"chaosengine apply failed: {proc.stderr}")
        return name

    def engine_status(self, namespace: str, name: str) -> str:
        proc = _kubectl("get", "chaosengine", name, "-n", namespace, "-o", "json")
        if proc.returncode != 0:
            return "missing"
        status = json.loads(proc.stdout).get("status", {})
        return status.get("engineStatus", "unknown")

    def chaos_result(self, namespace: str, engine: str, fault: str) -> dict:
        proc = _kubectl("get", "chaosresult", f"{engine}-{fault}", "-n", namespace, "-o", "json")
        if proc.returncode != 0:
            return {}
        status = json.loads(proc.stdout).get("status", {})
        return {
            "verdict": status.get("experimentStatus", {}).get("verdict"),
            "phase": status.get("experimentStatus", {}).get("phase"),
            "failStep": status.get("experimentStatus", {}).get("failStep"),
        }

    def delete_engine(self, namespace: str, name: str) -> None:
        _kubectl("delete", "chaosengine", name, "-n", namespace, "--ignore-not-found")
