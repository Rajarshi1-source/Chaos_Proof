"""LitmusChaos integration: generates and applies ChaosEngine CRs for any
installed fault, watches lifecycle, and deletes on every exit path (the full
cleanup saga arrives in Phase 5; Phase 2/3 already refuse to leave an engine
behind).

Network faults talk to the container runtime directly — CONTAINER_RUNTIME and
SOCKET_PATH are containerd on kind (the Docker shim died in K8s 1.24; saying
"Docker-level" dates the work by four years — experiment-suite skill)."""

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
{env_block}
"""

# Per-fault default env. Experiment YAML `params` override these.
FAULT_ENV_DEFAULTS: dict[str, dict[str, str]] = {
    "pod-delete": {
        "FORCE": "false",
        "PODS_AFFECTED_PERC": "50",
        # CHAOS_INTERVAL == duration -> a SINGLE kill event, matching a
        # "one replica is killed" hypothesis. Repeated kills every 10s would
        # test a different (much harsher) claim than the one written down.
    },
    "pod-network-latency": {
        "NETWORK_INTERFACE": "eth0",
        "NETWORK_LATENCY": "500",
        "PODS_AFFECTED_PERC": "100",
        "CONTAINER_RUNTIME": "containerd",
        "SOCKET_PATH": "/run/containerd/containerd.sock",
    },
    "pod-network-loss": {
        "NETWORK_INTERFACE": "eth0",
        "NETWORK_PACKET_LOSS_PERCENTAGE": "100",
        "PODS_AFFECTED_PERC": "100",
        "CONTAINER_RUNTIME": "containerd",
        "SOCKET_PATH": "/run/containerd/containerd.sock",
    },
}


def _kubectl(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", *args],
        input=input_text, capture_output=True, text=True, timeout=60,
    )


class LitmusClient:

    def apply_fault(self, fault: str, namespace: str, app: str,
                    duration_s: int, params: dict | None = None) -> str:
        env = {"TOTAL_CHAOS_DURATION": str(duration_s),
               "CHAOS_INTERVAL": str(duration_s)}          # single event by default
        env.update(FAULT_ENV_DEFAULTS.get(fault, {}))
        env.update({k: str(v) for k, v in (params or {}).items()})

        env_block = "\n".join(
            f'            - name: {k}\n              value: "{v}"'
            for k, v in env.items())
        name = f"{fault}-{int(time.time())}"
        manifest = ENGINE_TEMPLATE.format(
            name=name, namespace=namespace, app=app, fault=fault, env_block=env_block)
        proc = _kubectl("apply", "-f", "-", input_text=manifest)
        if proc.returncode != 0:
            raise RuntimeError(f"chaosengine apply failed: {proc.stderr}")
        return name

    def engine_status(self, namespace: str, name: str) -> str:
        proc = _kubectl("get", "chaosengine", name, "-n", namespace, "-o", "json")
        if proc.returncode != 0:
            return "missing"
        return json.loads(proc.stdout).get("status", {}).get("engineStatus", "unknown")

    def chaos_result(self, namespace: str, engine: str, fault: str) -> dict:
        proc = _kubectl("get", "chaosresult", f"{engine}-{fault}", "-n", namespace, "-o", "json")
        if proc.returncode != 0:
            return {}
        status = json.loads(proc.stdout).get("status", {})
        exp = status.get("experimentStatus", {})
        return {"verdict": exp.get("verdict"), "phase": exp.get("phase"),
                "failStep": exp.get("failStep")}

    def delete_engine(self, namespace: str, name: str) -> None:
        _kubectl("delete", "chaosengine", name, "-n", namespace, "--ignore-not-found")
