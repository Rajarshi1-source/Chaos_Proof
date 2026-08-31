"""Content-addressed evidence bundle storage (§22.1) — write-once, immutable.

The sha256 of a bundle's canonical JSON IS the run's identity. That single
sentence carries the design:

  * FILES ARE NAMED BY THEIR OWN DIGEST, so a path collision can only happen
    between byte-identical contents. Writing the same bundle twice is a no-op
    rather than an overwrite.
  * A WRITE THAT WOULD CHANGE AN EXISTING FILE IS A BUG, not an update. There
    is no `update_bundle`, and `write` refuses rather than clobbers.
  * THE FILE IS THE SOURCE OF TRUTH FOR REPLAY. The database holds an INDEX —
    which run has which digest — but `chaosctl replay` never touches it. That
    is what makes offline reproduction real rather than aspirational: no
    cluster, no network, no database, on a laptop with the wifi off.

The corpus under `evals/corpus/` is the same format, which is why the replay
eval and offline reproduction share one implementation instead of two that
drift.
"""

import json
import pathlib

from . import bundles

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
BUNDLE_DIR = REPO_ROOT / "bundles"


class BundleImmutabilityError(RuntimeError):
    """A write that would change a stored bundle.

    Content addressing makes this nearly impossible by construction — different
    content produces a different filename — so reaching it means the digest was
    computed over something other than the bytes being written. That is a
    correctness bug in the canonicalisation, and it must be loud.
    """


def path_for(sha: str, root: pathlib.Path | None = None) -> pathlib.Path:
    return (root or BUNDLE_DIR) / f"{sha}.json"


def write(bundle: dict, root: pathlib.Path | None = None) -> tuple[str, pathlib.Path]:
    """Seal, verify, and persist. Returns (sha256, path).

    Idempotent: storing a bundle that is already stored succeeds and changes
    nothing. Storing DIFFERENT content under an existing digest raises.
    """
    sealed = bundles.seal(bundle)
    sha = sealed["bundle_sha256"]
    target = path_for(sha, root)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Stored WITHOUT the self-referential `bundle_sha256` field, so the file's
    # own sha256 IS its filename. A digest computed over content that includes
    # the digest cannot match the file, and the first version of this stored the
    # sealed form and claimed in a comment that `sha256sum bundles/<sha>.json`
    # would match — it did not, by 64 characters. The claim is the valuable part
    # (anyone can verify a bundle with coreutils and no ChaosProof), so the
    # storage was changed to make it true rather than the comment softened.
    payload = bundles.canonical({k: v for k, v in sealed.items()
                                 if k != "bundle_sha256"})
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if existing != payload:
            raise BundleImmutabilityError(
                f"{target.name} already exists with different content. The digest "
                "is computed over the canonical JSON, so identical digests must "
                "mean identical bytes — this means canonicalisation is not "
                "deterministic, which would also break the replay eval's "
                "determinism anchor.")
        return sha, target

    target.write_text(payload, encoding="utf-8")
    return sha, target


def read(sha: str, root: pathlib.Path | None = None) -> dict:
    """Load a bundle and verify it still hashes to its own name.

    The verification is not paranoia: a bundle whose contents no longer match
    its digest is either corrupted or edited, and replaying it would produce
    output attributed to a run that did not happen.
    """
    target = path_for(sha, root)
    if not target.exists():
        raise FileNotFoundError(
            f"no bundle {sha[:12]} at {target}. Bundles are written by the runner "
            "at the end of an execution; a missing one means the run predates "
            "bundle storage or was never completed.")
    body = json.loads(target.read_text(encoding="utf-8"))

    # The filename is the claim; recomputing is the check.
    computed = bundles.digest(body)
    if computed != sha:
        raise BundleImmutabilityError(
            f"bundle {sha[:12]} does not hash to its own contents (computed "
            f"{computed[:12]}) — it has been modified since it was written. Its "
            "replay output would describe a run that did not happen.")

    # Re-attach the digest for consumers, who reasonably expect a bundle to
    # carry its own identity. It is derived on read rather than stored, which is
    # what keeps the file externally verifiable.
    return bundles.seal(body)


def resolve(prefix: str, root: pathlib.Path | None = None) -> str:
    """Expand a digest prefix, the way git does with short SHAs.

    An ambiguous prefix raises rather than picking one. Replaying the wrong run
    because two digests shared six characters would be a very hard bug to see:
    the output would be internally consistent and about the wrong execution.
    """
    root = root or BUNDLE_DIR
    if not root.exists():
        raise FileNotFoundError(f"no bundle directory at {root}")
    matches = sorted(p.stem for p in root.glob(f"{prefix}*.json"))
    if not matches:
        raise FileNotFoundError(f"no bundle matching {prefix!r} in {root}")
    if len(matches) > 1:
        raise ValueError(
            f"{prefix!r} is ambiguous — matches {len(matches)} bundles: "
            + ", ".join(m[:12] for m in matches[:5]))
    return matches[0]


def listing(root: pathlib.Path | None = None) -> list[dict]:
    root = root or BUNDLE_DIR
    if not root.exists():
        return []
    out = []
    for path in sorted(root.glob("*.json")):
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"sha256": path.stem, "experiment": bundle.get("experiment"),
                    "verdict": bundle.get("verdict"),
                    "git_sha": bundle.get("git_sha"),
                    "epoch": (bundle.get("epoch") or {}).get("epoch_sha256")})
    return out


def verify_all(root: pathlib.Path | None = None) -> tuple[int, list[str]]:
    """`chaosctl audit --verify-bundles`: re-hash everything, report mismatches."""
    root = root or BUNDLE_DIR
    problems = []
    checked = 0
    for path in sorted((root or BUNDLE_DIR).glob("*.json")):
        checked += 1
        try:
            read(path.stem, root)
        except Exception as exc:                                   # noqa: BLE001
            problems.append(f"{path.stem[:12]}: {exc}")
    return checked, problems


# --------------------------------------------------------------------------- #
# The database INDEX. Never the source of truth for replay.
# --------------------------------------------------------------------------- #

def index(cur, execution_id: int, sha: str) -> None:
    """Link an execution to its bundle.

    Write-once at this level too: an execution that already has a bundle digest
    keeps it. A run's identity cannot be reassigned after the fact.
    """
    cur.execute(
        """UPDATE experiment_executions
              SET bundle_sha256 = %s
            WHERE id = %s AND bundle_sha256 IS NULL""",
        (sha, execution_id))


def sha_for_execution(cur, execution_id: int) -> str | None:
    cur.execute("SELECT bundle_sha256 FROM experiment_executions WHERE id = %s",
                (execution_id,))
    row = cur.fetchone()
    return row[0] if row else None
