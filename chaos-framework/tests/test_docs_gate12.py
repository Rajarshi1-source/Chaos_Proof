"""GATE 12, as a test rather than a one-off rehearsal.

The gate is: *the 5-minute demo runs end to end without a recovery, and the
README's grid contains a paragraph naming where each competitor wins.*

The second clause is a claim about a file, and a claim about a file rots. The
"where they win" paragraph is the one part of the README with an incentive to
disappear during a tidy-up — it is the only place the project admits to losing —
so it is asserted here and re-asserted on every PR. Same for the demo's commands
resolving, the seven ADRs each naming a reversal condition, and the model card
carrying a gating status.

Hermetic: reads files, runs no cluster.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def flat(text: str) -> str:
    """Collapse whitespace before matching prose.

    These files are hard-wrapped at 80 columns, so a sentence that reads as one
    phrase is several lines in the source. Asserting against the raw text tests
    where the line breaks fell, which changes every time someone reflows a
    paragraph — a test that fails on reformatting teaches people to delete it.
    """
    return " ".join(text.split())


README = (ROOT / "README.md").read_text(encoding="utf-8")
DEMO = (ROOT / "docs" / "DEMO.md").read_text(encoding="utf-8")
CARD = (ROOT / "EXPERIMENTS.md").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
ADR_DIR = ROOT / "docs" / "adr"


def grid_columns() -> list[str]:
    header = [l for l in README.splitlines() if l.startswith("| Capability |")]
    assert len(header) == 1, "expected exactly one head-to-head grid"
    return [c.strip().strip("*") for c in header[0].split("|")[2:-1]]


def where_they_win() -> str:
    m = re.search(r"### Where they win\n(.*?)\n---", README, re.S)
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- #
# GATE 12, clause 2 — the grid and its honest paragraph.
# --------------------------------------------------------------------------- #

def test_the_grid_lists_six_competitors_and_chaosproof():
    cols = grid_columns()
    assert cols[-1] == "ChaosProof"
    assert len(cols) == 7


def test_the_paragraph_names_a_win_for_every_competitor():
    """A table with no losses reads as marketing. Every column that is not
    ChaosProof must be named in the prose as beating it at something."""
    para = where_they_win()
    assert para, "the 'Where they win' paragraph is missing"
    missing = [c for c in grid_columns()[:-1] if c.split(" (")[0] not in para]
    assert not missing, f"no stated win for: {missing}"


def test_the_paragraph_concedes_rather_than_hedges():
    """Specifically that ChaosProof is not a replacement, and that it runs ON
    the thing it is being compared to."""
    para = flat(where_they_win())
    assert "not a replacement for any of them" in para
    assert "would be strictly worse without it" in para


def test_the_grid_records_chaosproofs_own_losses():
    """Two rows where the answer is a plain no. If both ever turn into ticks,
    something has been marked done that was not."""
    assert "| Multi-tenancy, HA injection, VM chaos |" in README
    assert "❌ 12-week project" in README


def test_the_scale_weakness_is_stated_and_the_citation_is_hedged():
    """Flipkart's four customisations are cited from CNCF's project update, not
    from a talk anyone here watched. Claiming otherwise is the kind of detail an
    interviewer checks."""
    para = where_they_win()
    assert "talk describes" in para
    assert "not been independently reviewed" in para


# --------------------------------------------------------------------------- #
# GATE 12, clause 1 — the demo.
# --------------------------------------------------------------------------- #

def test_the_demo_has_six_beats():
    assert len(re.findall(r"^## \d ·", DEMO, re.M)) == 6


def test_the_demo_leads_with_the_projects_own_bug():
    """The order is the point. An engineer who opens by showing how their
    tooling used to lie to them has established credibility no green dashboard
    can buy — and reordering this would quietly throw that away."""
    assert DEMO.index("lead with your own bug") < DEMO.index("A real experiment")
    assert DEMO.index("INVALID") < DEMO.index("HYPOTHESIS_HELD")


def test_every_make_command_in_the_demo_resolves():
    targets = set(re.findall(r"^([a-z][a-z0-9-]*):", MAKEFILE, re.M))
    blocks = re.findall(r"^```bash\n(.*?)\n```", DEMO, re.S | re.M)
    cmds = [c.strip() for b in blocks for c in b.splitlines() if c.strip()]
    missing = [c for c in cmds if c.startswith("make ") and c.split()[1] not in targets]
    assert not missing, f"demo commands with no Makefile target: {missing}"


def test_no_makefile_target_is_a_stub_that_exits_one():
    """`make dashboard` was a Phase 6 stub that echoed and exited 1 while the
    README quick start listed it as a step. A demo command that cannot run is
    worse than an absent one, because it is discovered in the room."""
    assert "@exit 1" not in MAKEFILE


def test_the_demo_replays_a_bundle_that_actually_exists():
    """The plan's example sha is illustrative. If the demo quotes one, it has to
    be in `bundles/` or beat 6 fails live, offline, with no way to recover."""
    shas = re.findall(r"chaosctl replay ([0-9a-f]{8,64})", DEMO + README)
    assert shas, "no replay command in the demo or README"
    stored = {p.stem for p in (ROOT / "bundles").glob("*.json")}
    for sha in shas:
        assert any(s.startswith(sha) for s in stored), f"no bundle for {sha}"


def test_the_demo_states_a_recovery_for_each_failure_mode():
    """GATE 12 asks for a demo that runs 'without a recovery'. The table exists
    so that if one is needed, it is a sentence rather than improvisation."""
    assert "## Recovery notes" in DEMO
    assert "a failing experiment is not a failure of the demo" in flat(DEMO)


# --------------------------------------------------------------------------- #
# The ADRs and the model card.
# --------------------------------------------------------------------------- #

def adr_files() -> list[pathlib.Path]:
    return sorted(ADR_DIR.glob("00*.md"))


def test_there_are_seven_adrs():
    assert len(adr_files()) == 7


@pytest.mark.parametrize("adr", adr_files(), ids=lambda p: p.stem)
def test_every_adr_names_its_reversal_condition(adr):
    """A decision with no stated reversal condition is a preference wearing a
    decision's clothes — nobody who was not in the room can revisit it."""
    assert "reversal condition" in adr.read_text(encoding="utf-8").lower()


# The alternative each ADR turned down, by name. Asserting a generic word like
# "rejected" is gameable and was: three of these ADRs discuss their alternative
# under a different heading and passed nothing. Naming the actual competitor is
# the assertion worth making, because an ADR that stops mentioning what it beat
# has become a description of what was built.
REJECTED_ALTERNATIVE = {
    "001-litmuschaos-over-alternatives": ["chaos mesh", "gremlin", "xk6-disruptor"],
    "002-plain-postgresql-over-timescaledb": ["timescaledb", "mongodb", "cassandra"],
    "003-open-workload-model": ["closed model", "constant-vus"],
    "004-sequential-experiment-execution": ["parallel"],
    "005-resilience4j-over-native-spring": ["@retryable", "@concurrencylimit"],
    "006-client-side-slis-as-primary": ["server-side metrics"],
    "007-bounded-bisection-with-visible-cost": ["automatically", "option a"],
}


@pytest.mark.parametrize("adr", adr_files(), ids=lambda p: p.stem)
def test_every_adr_names_the_option_it_rejected(adr):
    text = flat(adr.read_text(encoding="utf-8")).lower()
    expected = REJECTED_ALTERNATIVE[adr.stem]
    missing = [e for e in expected if e not in text]
    assert not missing, f"{adr.stem} no longer names: {missing}"


def test_the_readme_links_every_adr():
    for adr in adr_files():
        assert f"docs/adr/{adr.name}" in README, f"{adr.name} is not linked"


def test_the_model_card_states_gating_status_for_every_experiment():
    """The whole point of the card. An experiment whose gating status is not
    stated is one nobody can tell is blocking their merge."""
    for name in ("pod_kill_payment_svc", "network_latency_payment",
                 "network_partition_payment", "disk_fill_inventory",
                 "cpu_spike_payment", "container_kill_payment"):
        assert name in CARD, f"{name} missing from EXPERIMENTS.md"
    assert "advisory" in CARD


def test_the_model_card_admits_what_is_not_measured():
    """Sigma is uncharacterised for every experiment, so nothing gates. Stating
    that is the difference between a model card and a brochure."""
    assert "Every experiment in this repository is advisory" in CARD
    assert "not carried into the table above" in CARD


def test_the_model_card_carries_the_three_verdict_flipping_corrections():
    """disk-fill asserts eviction, cpu-hog never asserts the spike disappears,
    container-kill is containerd. Each produces a confident WRONG verdict if
    missed, so each is written down where someone editing an experiment reads."""
    assert "not `NodeDiskPressure`" in CARD
    assert "never asserts that the CPU spike disappears" in CARD
    assert "CRI/containerd, not Docker" in CARD
