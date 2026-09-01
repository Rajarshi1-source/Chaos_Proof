# The ChaosProof framework image — the artifact `build-sign-deploy` builds,
# signs with cosign, and Helm rolls out.
#
# Pinned by digest, not by tag. `python:3.14-slim` is a moving tag: the image
# behind it changes weekly, so two builds of the same commit would produce
# different images and the cosign signature would attest to something that
# cannot be reproduced. A resilience project that cannot rebuild its own
# artifact bit-for-bit is not making a claim it can defend.
#
# Refresh procedure (deliberately manual, so it appears in a review):
#   docker buildx imagetools inspect python:3.14.6-slim-bookworm --format '{{ .Manifest.Digest }}'
FROM python:3.14.6-slim-bookworm@sha256:4c92ffcde4dd6f1ff72a24518f49fd4990b27134987dfa31a733badde66df9f8 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so a source-only change does not re-resolve the world.
COPY chaos-framework/pyproject.toml ./chaos-framework/pyproject.toml
# `cel-python`, NOT `celpy`. The distribution is `cel-python`; `celpy` is only
# the import name, and a different project owns it on PyPI. Installing the wrong
# one leaves policy.py on its fail-closed path, where all nine safety rules deny
# - including their must-allow cases. d382314 fixed that in the workflow and
# added a guard test, but the guard only read the workflow, so THIS line kept
# the bug and shipped it in the signed image.
RUN pip install --no-cache-dir "requests>=2.32" "psycopg[binary]>=3.2" "pyyaml>=6.0" \
        "fastapi>=0.128" "uvicorn>=0.40" "cel-python>=0.5"

# The framework, plus the three things it reads at runtime as DATA rather than
# code: the experiment specs, the CEL safety policy, and the load profiles.
# Baking them in is what makes an image reproducible on its own — a framework
# that reads its guardrails from a mounted volume can be silently defanged.
COPY chaos-framework/src ./src
COPY experiments ./experiments
COPY policy ./policy
COPY profiles ./profiles
COPY ci ./ci

# Non-root, matching the chart's securityContext (runAsUser: 10001). The
# framework's job is to observe and clean up; it has no reason to be root, and
# a chaos tool running as root in a cluster is a bad look before it is a bad
# risk.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin chaosproof \
    && chown -R chaosproof:chaosproof /app
USER 10001

# No default CMD that injects anything. The chart supplies the command: the API
# server for the framework Deployment, `src.cleanup_sweeper` for the CronJob.
# An image whose default entrypoint can inject a fault is one `docker run` away
# from being a very bad afternoon.
CMD ["python", "-m", "src.chaosctl", "list"]
