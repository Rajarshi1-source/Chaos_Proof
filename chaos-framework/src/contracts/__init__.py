"""Service resilience contracts (§19).

Consumer-driven contract testing applied to FAILURE rather than to schemas:
each service declares what it promises, what it absorbs from its dependencies,
and what it promises not to inflict on them. ChaosProof generates one
experiment per clause, so the suite is derived from declared architecture
rather than from a human remembering to write a test.
"""
