"""Threshold constants — ONE module, and changing any of them opens a scoring
epoch (Part 1 of IMPLEMENTATION_PLAN.md; mlops-quality skill)."""

# Validity (measurement-integrity)
MIN_SAMPLE_COVERAGE = 0.90
DROPPED_CEILING = 10
STEADY_STATE_WINDOW_S = 120
RECOVERY_HOLD_S = 30

# Golden signals
THROTTLE_RATIO_CEILING = 0.05
PSI_FULL_CEILING = 0.05
RESTART_TOLERANCE = 0

# Measurement
TARGET_SCRAPE_INTERVAL_S = 5
SLI_WINDOW_S = 30
SAMPLE_INTERVAL_S = 5

# Scoring (hypothesis-engine; the scorer itself lands in Phase 4)
WEIGHTS = {
    "slo_recovery": 0.35,
    "alert_validation": 0.25,
    "resilience_pattern": 0.25,
    "recovery_completeness": 0.15,
}
PASS_THRESHOLD = 0.80
FAIL_THRESHOLD = 0.50

# Quality (mlops-quality)
FLAKINESS_WINDOW = 20
MAX_SCORE_STDDEV = 0.05
MAX_VERDICT_FLIP_RATE = 0.05

# Safety (safety-plane)
BUDGET_WARN_PCT = 50.0
BUDGET_FREEZE_PCT = 80.0
MAX_BUDGET_BURN_PCT = 2.0
# The single probe a half-open chaos breaker allows through must be low-radius,
# or the trial that decides recovery is itself the next outage: it aborts, the
# breaker reopens, and nothing was learned about whether the system had settled.
#
# DERIVED FROM THE ACTUAL SCORE DISTRIBUTION, not copied from the policy file's
# require-override threshold of 60 — which is what it was first, and that made
# the recovery path UNREACHABLE. blast score is
#     affected_pods + 10*namespaces + 25*user_facing + int(50*replica_fraction)
# so the SMALLEST experiment this system can express (1 pod, 1 namespace,
# user-facing, 50% of 2 replicas) already scores 61, and a full partition
# scores 87. A ceiling of 60 sat below the entire range: no experiment could
# ever be the trial, so the breaker could only recover by waiting out its own
# 24h window. 65 admits the single-pod class and still excludes the
# full-partition class, which is the distinction "low-radius" is meant to draw.
HALF_OPEN_MAX_BLAST_SCORE = 65
