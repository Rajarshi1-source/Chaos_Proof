"""The SLI validity gate. A verdict is only scoreable if the measurement was
capable of detecting failure. INVALID is a third verdict, distinct from pass
and fail; it never contributes to a score — not as zero, not as partial credit.

Each check has a DISTINCT invalidity_reason: 'the system failing' and 'the
generator failing' and 'the sampler failing' are three different findings."""

from dataclasses import dataclass, field

from ..constants import DROPPED_CEILING, MIN_SAMPLE_COVERAGE


@dataclass
class LoadFacts:
    achieved_rps: float          # mean client rps over the measured window
    dropped_iterations: float    # generator saturation counter
    coverage: float              # fraction of ticks where the client source had data
    invalidity_reason: str | None = field(default=None)

    def is_valid(self, min_rps_floor: float) -> bool:
        if self.achieved_rps < min_rps_floor:
            self.invalidity_reason = (
                f"achieved {self.achieved_rps:.0f} rps, floor is "
                f"{min_rps_floor:.0f} — cannot measure availability"
            )
            return False
        if self.dropped_iterations > DROPPED_CEILING:
            self.invalidity_reason = (
                "k6 could not sustain the arrival rate — the load "
                "generator was the bottleneck, not the system"
            )
            return False
        if self.coverage < MIN_SAMPLE_COVERAGE:
            self.invalidity_reason = (
                f"only {self.coverage:.0%} sample coverage in the window"
            )
            return False
        return True
