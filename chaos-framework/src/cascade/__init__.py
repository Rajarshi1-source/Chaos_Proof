"""Cascading failure scenarios (§18).

Real outages are cascades, not single faults. A scenario is a DAG of faults
with temporal AND conditional triggers, and its most valuable output is a
trigger that never fires — proof that an upstream failure was absorbed rather
than amplified.
"""
