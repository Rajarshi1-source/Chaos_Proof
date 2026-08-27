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
{probe_block}"""


# In-band abort path. mode: Continuous + stopOnFailure: true is what turns a
# probe into an ABORT — mode: EOT only tells you afterwards, which is a
# validator, not a guard.
#
# Both probes are promProbe, which is why Litmus >= 3.28.0 is load-bearing here:
# that release fixed a stale-config leak across MULTIPLE PROBES OF THE SAME TYPE.
# On an older build the second probe could silently inherit the first's query.
#
# The queries read k6's remote-written series, not server-side metrics: a guard
# reading server-side counters cannot see the failures that never reached a server.
PROBE_TEMPLATE = """        probe:
          - name: client-availability-guard
            type: promProbe
            mode: Continuous
            runProperties:
              probeTimeout: 5s
              interval: 5s
              retry: 1
              stopOnFailure: true
            promProbe/inputs:
              endpoint: {prom_endpoint}
              query: 1 - avg(k6_http_req_failed_rate{{testrun="{testrun}"}})
              # NO `type:` here. Verified against the Litmus 3.31.0 CRD:
              # promProbe/inputs.comparator accepts only criteria and value,
              # while cmdProbe REQUIRES type. Including it fails strict decoding
              # with 'unknown field ... comparator.type' at apply time.
              comparator:
                criteria: ">="
                value: "{availability_floor}"
          - name: blast-radius-containment
            type: promProbe
            mode: Continuous
            runProperties:
              probeTimeout: 5s
              interval: 10s
              retry: 0
              stopOnFailure: true
            promProbe/inputs:
              endpoint: {prom_endpoint}
              query: >-
                sum(rate(http_server_requests_seconds_count{{status=~"5..",namespace!="{target_ns}"}}[30s]))
                or vector(0)
              comparator:
                criteria: "<="
                value: "{containment_ceiling}"
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
                    duration_s: int, params: dict | None = None,
                    probes: str = "") -> str:
        env = {"TOTAL_CHAOS_DURATION": str(duration_s),
               "CHAOS_INTERVAL": str(duration_s)}          # single event by default
        env.update(FAULT_ENV_DEFAULTS.get(fault, {}))
        env.update({k: str(v) for k, v in (params or {}).items()})

        env_block = "\n".join(
            f'            - name: {k}\n              value: "{v}"'
            for k, v in env.items())
        name = f"{fault}-{int(time.time())}"
        manifest = ENGINE_TEMPLATE.format(
            name=name, namespace=namespace, app=app, fault=fault,
            env_block=env_block, probe_block=probes)
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

    def probes_for(self, spec: dict, testrun: str, prom_endpoint: str,
                   target_ns: str = "target-app") -> str:
        """Render the abort probes from the SAME abort_conditions the watchdog
        reads, so the in-band and out-of-band paths cannot drift apart."""
        hyp = spec.get("hypothesis", {})
        conditions = spec.get("abort_conditions") or hyp.get("abort_conditions") or []
        # abort_conditions state the TRIGGER ("availability < 0.80"); a Litmus
        # probe states the HEALTHY criteria and aborts when it fails
        # ("availability >= 0.80"). Same number, inverted comparator — read the
        # threshold from the shared declaration so the two paths cannot drift.
        availability_floor, containment_ceiling = 0.80, 0.5
        for c in conditions:
            if c.get("metric") == "client_availability":
                availability_floor = float(c["threshold"])
            elif c.get("metric") == "other_namespace_5xx_rate":
                containment_ceiling = float(c["threshold"])
        return PROBE_TEMPLATE.format(
            prom_endpoint=prom_endpoint, testrun=testrun, target_ns=target_ns,
            availability_floor=availability_floor,
            containment_ceiling=containment_ceiling)

    def stop_engine(self, namespace: str, name: str) -> None:
        """Halt the fault mid-flight by flipping engineState to stop. This is the
        abort itself: it ends the injection without waiting for the fault's own
        schedule, which is the whole difference between a guard and a report."""
        _kubectl("patch", "chaosengine", name, "-n", namespace, "--type", "merge",
                 "-p", '{"spec":{"engineState":"stop"}}')

    def delete_engine(self, namespace: str, name: str) -> None:
        """--wait=false because Litmus clears the ChaosEngine's finalizer
        asynchronously after the experiment stops. A blocking delete issued
        immediately after an abort waits on that finalizer and times out, turning
        a successful abort into a failed cleanup step. The chaos-cleanup CronJob
        is the backstop for any engine whose finalizer never clears."""
        _kubectl("delete", "chaosengine", name, "-n", namespace,
                 "--ignore-not-found", "--wait=false")
