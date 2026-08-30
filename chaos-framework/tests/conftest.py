"""Path setup only. The `unit` job must run without a cluster, a database, or a
network — a gate a developer cannot fail cheaply on their laptop is a gate they
learn to wait for the chaos job to tell them about."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
