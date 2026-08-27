"""The fenced experiment lock.

Only one experiment may run at a time, cluster-wide — that is the project's
first documented scaling decision, and every result depends on it: two
overlapping experiments make every SLI unattributable, which invalidates the
entire dataset rather than one run.

**The lock must be FENCED.** Rev 1 used a bare `SET NX` with a TTL. A runner
that stalls past the TTL — a long GC pause, a suspended laptop — resumes
believing it still holds the lock while a successor is already injecting. The
value is therefore the execution id, and ownership is re-verified before every
mutating call. Losing the lock is a hard stop, not a warning.

Falls back to a local file lock when Redis is absent, so a single-node dev
environment still gets mutual exclusion rather than silently getting none.
"""

import os
import pathlib
import tempfile
import time
from dataclasses import dataclass

LOCK_TTL_S = 900          # >= the longest experiment, incl. cleanup
_FALLBACK_DIR = pathlib.Path(tempfile.gettempdir()) / "chaosproof-locks"


class LockLost(RuntimeError):
    """Raised when ownership check fails. Never downgraded to a warning: acting
    without the lock is how two experiments overlap."""


@dataclass
class FencedLock:
    namespace: str
    fence: str                 # the execution id — the fencing token
    _redis: object | None = None
    _path: pathlib.Path | None = None

    @property
    def key(self) -> str:
        return f"chaos:lock:{self.namespace}"

    def acquire(self) -> bool:
        try:
            import redis                                    # noqa: PLC0415
            url = os.environ.get("CHAOSPROOF_REDIS")
            if url:
                self._redis = redis.Redis.from_url(url, decode_responses=True)
                return bool(self._redis.set(self.key, self.fence, nx=True, ex=LOCK_TTL_S))
        except Exception:
            self._redis = None

        # File fallback — mutual exclusion on one host, honestly labelled.
        _FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        self._path = _FALLBACK_DIR / f"{self.namespace}.lock"
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # Honour the TTL so a crashed runner cannot wedge the namespace forever.
            try:
                age = time.time() - self._path.stat().st_mtime
                if age > LOCK_TTL_S:
                    self._path.unlink(missing_ok=True)
                    fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                else:
                    return False
            except OSError:
                return False
        with os.fdopen(fd, "w") as f:
            f.write(self.fence)
        return True

    def verify(self) -> None:
        """Re-verify ownership BEFORE every mutating call. This is the fence."""
        holder = None
        if self._redis is not None:
            holder = self._redis.get(self.key)
        elif self._path is not None and self._path.exists():
            holder = self._path.read_text().strip()

        if holder != self.fence:
            raise LockLost(
                f"lock {self.key} is held by {holder!r}, not this execution "
                f"({self.fence!r}) — refusing to act. An unfenced runner resuming "
                "after a stall is how two experiments overlap and every result "
                "becomes unattributable.")

    def release(self) -> None:
        """Only release a lock we still own — never stomp a successor's."""
        try:
            self.verify()
        except LockLost:
            return
        if self._redis is not None:
            self._redis.delete(self.key)
        elif self._path is not None:
            self._path.unlink(missing_ok=True)
