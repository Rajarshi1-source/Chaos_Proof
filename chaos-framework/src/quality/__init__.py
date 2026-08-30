"""The MLOps and quality wrapper (§21).

The framing that makes this coherent: ChaosProof's scorer is a deterministic
model that maps evidence to a number, so it gets the treatment a model gets —
versioned artefact, labelled dataset, offline eval gating CI, data validation,
shadow deployment, progressive rollout, drift monitoring, rollback.

Deliberately no machine learning. Every decision here must be unit-testable,
replayable, and hashable.
"""
