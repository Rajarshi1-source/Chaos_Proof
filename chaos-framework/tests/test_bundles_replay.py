"""Evidence bundles, offline replay, and the narrator boundary (§22).

GATE 11 in one sentence: `chaosctl replay <sha>` with the wifi off produces
byte-identical output to the stored bundle. Everything here defends some part
of that.
"""

import json
import pathlib
import socket

import pytest

from src.postmortem import narrator as N
from src.quality import bundle_store, bundles, replay

CORPUS = pathlib.Path(__file__).resolve().parents[2] / "evals" / "corpus"


@pytest.fixture(scope="module")
def sample_bundle() -> dict:
    case = json.loads((CORPUS / "breaker_never_opens.json").read_text(encoding="utf-8"))
    return case["bundle"]


# --------------------------------------------------------------------------- #
# Content addressing.
# --------------------------------------------------------------------------- #

def test_the_digest_is_over_the_evidence_not_the_digest_field(sample_bundle):
    """A bundle carries its own digest, so the digest must be computed with that
    field removed — otherwise sealing would change what it hashes."""
    assert bundles.verify(sample_bundle)
    resealed = bundles.seal(sample_bundle)
    assert resealed["bundle_sha256"] == sample_bundle["bundle_sha256"]


def test_changing_any_evidence_changes_the_digest(sample_bundle):
    tampered = dict(sample_bundle)
    tampered["verdict"] = "held"
    assert bundles.digest(tampered) != sample_bundle["bundle_sha256"]


def test_key_order_does_not_change_the_digest(sample_bundle):
    """Canonicalisation sorts keys. Two structurally identical bundles must hash
    alike or the determinism anchor fires on serialisation noise."""
    reordered = dict(reversed(list(sample_bundle.items())))
    assert bundles.digest(reordered) == bundles.digest(sample_bundle)


def test_floats_are_rounded_so_platforms_agree():
    """An unrounded float differing in its 17th decimal between two machines
    would break the digest with no behaviour change at all."""
    a = bundles.digest({"v": 0.1 + 0.2})
    b = bundles.digest({"v": 0.30000000000000004})
    assert a == b


# --------------------------------------------------------------------------- #
# Write-once storage.
# --------------------------------------------------------------------------- #

def test_storing_the_same_bundle_twice_is_a_no_op(tmp_path, sample_bundle):
    sha1, path1 = bundle_store.write(sample_bundle, tmp_path)
    sha2, path2 = bundle_store.write(sample_bundle, tmp_path)
    assert sha1 == sha2 and path1 == path2


def test_the_file_is_named_by_its_own_digest(tmp_path, sample_bundle):
    sha, path = bundle_store.write(sample_bundle, tmp_path)
    assert path.stem == sha


def test_the_file_hashes_to_its_own_filename(tmp_path, sample_bundle):
    """`sha256sum bundles/<sha>.json` MUST equal the filename, so a reader can
    verify a bundle with coreutils and no ChaosProof.

    The first implementation stored the sealed form — including the
    self-referential `bundle_sha256` field — and asserted this property only
    after stripping that field back out. The test passed and the claim in the
    comment beside it was false by 64 characters. Asserting the raw file bytes
    is the only version of this test that checks what a reader would check.
    """
    import hashlib
    sha, path = bundle_store.write(sample_bundle, tmp_path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha


def test_a_read_bundle_still_carries_its_identity(tmp_path, sample_bundle):
    """Derived on read rather than stored, so the file stays verifiable while
    consumers still get a bundle that knows its own digest."""
    sha, _ = bundle_store.write(sample_bundle, tmp_path)
    loaded = bundle_store.read(sha, tmp_path)
    assert loaded["bundle_sha256"] == sha
    assert bundles.verify(loaded)


def test_a_modified_bundle_is_refused_on_read(tmp_path, sample_bundle):
    """Replaying it would produce output attributed to a run that never happened."""
    sha, path = bundle_store.write(sample_bundle, tmp_path)
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["verdict"] = "held"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(bundle_store.BundleImmutabilityError, match="modified"):
        bundle_store.read(sha, tmp_path)


def test_there_is_no_update_function():
    """Bundles are write-once. The absence of the function IS the enforcement."""
    assert not hasattr(bundle_store, "update")
    assert not hasattr(bundles, "update_bundle")


def test_an_ambiguous_prefix_raises_rather_than_guessing(tmp_path):
    """Replaying the wrong run because two digests shared six characters would
    be a very hard bug to see: the output is internally consistent and about
    the wrong execution."""
    (tmp_path / "abc111.json").write_text("{}", encoding="utf-8")
    (tmp_path / "abc222.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="ambiguous"):
        bundle_store.resolve("abc", tmp_path)


def test_a_unique_prefix_resolves(tmp_path, sample_bundle):
    sha, _ = bundle_store.write(sample_bundle, tmp_path)
    assert bundle_store.resolve(sha[:10], tmp_path) == sha


# --------------------------------------------------------------------------- #
# GATE 11 — offline, byte-identical.
# --------------------------------------------------------------------------- #

def test_replay_is_byte_identical_across_invocations(sample_bundle):
    """The output format doubles as a golden-file test and the dashboard renders
    it. Rendering twice must produce the same bytes."""
    first = replay.render(sample_bundle)
    second = replay.render(sample_bundle)
    assert first == second
    assert first.encode("utf-8") == second.encode("utf-8")


def test_replay_opens_no_socket(sample_bundle, monkeypatch):
    """GATE 11 says 'with wifi off'. Rather than trusting that, make any socket
    creation fail and confirm replay still renders."""
    def refuse(*args, **kwargs):
        raise AssertionError("replay attempted a network connection")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    text = replay.render(sample_bundle)
    assert "experiment :" in text
    assert sample_bundle["bundle_sha256"] in text


def test_replay_reads_the_verdict_rather_than_recomputing_it(sample_bundle):
    """Re-deriving here would make the output depend on the CURRENT scorer, so
    replaying an old bundle after a scoring change would print a number that run
    never produced."""
    doctored = dict(sample_bundle)
    doctored["verdict"] = "aborted"
    assert "ABORTED" in replay.render(doctored)


def test_replay_renders_the_load_plane_before_the_verdict(sample_bundle):
    """A run with no load has no evidence, so the load line comes first — the
    same ordering discipline as the runner itself."""
    text = replay.render(sample_bundle)
    assert text.index("load       :") < text.index("verdict    :")


def test_replay_marks_an_unmeasurable_run_invalid():
    case = json.loads((CORPUS / "no_load_zero_traffic.json").read_text(encoding="utf-8"))
    assert "INVALID" in replay.render(case["bundle"])


def test_replay_states_no_score_rather_than_zero():
    """INVALID produces no score at all, not a zero — including in the rendering."""
    case = json.loads((CORPUS / "no_load_zero_traffic.json").read_text(encoding="utf-8"))
    text = replay.render(case["bundle"])
    assert "score none" in text


def test_every_corpus_bundle_replays(tmp_path):
    """25 committed bundles, each rendered without a cluster, network or DB."""
    rendered = 0
    for path in sorted(CORPUS.glob("*.json")):
        bundle = json.loads(path.read_text(encoding="utf-8"))["bundle"]
        text = replay.render(bundle)
        assert bundle["bundle_sha256"] in text
        rendered += 1
    assert rendered >= 20


# --------------------------------------------------------------------------- #
# The narrator boundary.
# --------------------------------------------------------------------------- #

def test_the_default_narrator_is_the_template_one():
    """The system must work with no API key and no network. A portfolio project
    that breaks without a paid API is a liability in a live demo."""
    assert isinstance(N.default(), N.TemplateNarrator)


def test_the_template_narrator_needs_no_provider(sample_bundle):
    draft = N.TemplateNarrator().draft(sample_bundle)
    assert draft.source == "template"
    assert draft.summary


def test_an_llm_narrator_with_no_provider_falls_back_silently(sample_bundle):
    """No provider configured is the NORMAL case, not an error."""
    draft = N.LLMNarrator().draft(sample_bundle)
    assert draft.source == "template"


def test_a_draft_inventing_a_number_is_discarded(sample_bundle):
    """A hallucinated postmortem is worse than a templated one because it is
    more convincing."""
    before = N.DISCARDS["total"]

    def invent(prompt, model=None):
        return "Availability fell to 47.3% and recovery took 812 seconds."

    draft = N.LLMNarrator(complete=invent, model="test").draft(sample_bundle)
    assert draft.discarded is True
    assert draft.source == "template"          # fell back
    assert "47.3" in draft.discard_reason
    assert N.DISCARDS["total"] == before + 1


def test_a_grounded_draft_is_kept(sample_bundle):
    achieved = sample_bundle["load"]["achieved_rps"]

    def grounded(prompt, model=None):
        return f"The load plane achieved {achieved} rps and the breaker never opened."

    draft = N.LLMNarrator(complete=grounded, model="test").draft(sample_bundle)
    assert draft.discarded is False
    assert draft.source.startswith("llm:")


def test_a_provider_error_falls_back_and_counts(sample_bundle):
    before = N.DISCARDS["total"]

    def boom(prompt, model=None):
        raise RuntimeError("upstream 503")

    draft = N.LLMNarrator(complete=boom, model="test").draft(sample_bundle)
    assert draft.source == "template"
    assert N.DISCARDS["total"] == before + 1


def test_discards_are_exported_as_a_metric():
    text = N.metrics()
    assert "chaosproof_narrator_discards_total" in text


def test_the_narrator_never_emits_a_verdict_field(sample_bundle):
    """The LLM never touches a verdict. The Draft type has no verdict field at
    all, so there is nothing for one to leak through."""
    draft = N.TemplateNarrator().draft(sample_bundle)
    assert not hasattr(draft, "verdict")
    assert not hasattr(draft, "score")


def test_the_model_id_is_not_hardcoded():
    """Provider-agnostic adapter, model id from config, never a vendor at the
    call site."""
    source = pathlib.Path(N.__file__).read_text(encoding="utf-8").lower()
    for vendor in ("gpt-", "claude-", "gemini", "api.openai.com", "anthropic.com"):
        assert vendor not in source, f"{vendor} is hardcoded in the narrator"
