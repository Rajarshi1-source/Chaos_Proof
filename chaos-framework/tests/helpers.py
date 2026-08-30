"""Sample-set construction. Deliberately allows `None` in a series: a real
measurement gap is the case every validator has to get right, and a fixture
that cannot express one hides the bug it should catch."""

from src.measurement.sampler import Sample, SampleSet


def make_samples(client_series: dict[str, list],
                 server_series: dict[str, list] | None = None,
                 *, start: float = 1_000.0, interval_s: float = 5.0) -> SampleSet:
    server_series = server_series or {}
    lengths = {len(v) for v in list(client_series.values()) + list(server_series.values())}
    assert len(lengths) == 1, "all series must have the same number of ticks"
    n = lengths.pop()

    ss = SampleSet()
    for i in range(n):
        ss.add(Sample(
            sampled_at=start + i * interval_s,
            client={k: v[i] for k, v in client_series.items()},
            server={k: v[i] for k, v in server_series.items()},
        ))
    return ss
