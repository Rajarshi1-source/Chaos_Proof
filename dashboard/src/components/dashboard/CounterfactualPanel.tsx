import type { CounterfactualPattern, CounterfactualResponse } from '@/types';

/**
 * The ROI panel (§17) — what each resilience pattern is actually worth.
 *
 * THE RULE THIS COMPONENT EXISTS TO ENFORCE: never show a counterfactual delta
 * when the interquartile ranges overlap. Reporting a median delta drawn from
 * overlapping distributions is how dashboards become fiction, and the restraint
 * is the point of the panel rather than a limitation of it.
 *
 * The enforcement is structural rather than a rule someone has to remember. The
 * API returns `deltaMedian: null` for an inconclusive verdict, and this
 * component reads that field — it never subtracts the two medians itself, even
 * though both are right there and the arithmetic is trivial. A renderer that
 * can compute the forbidden number is one refactor away from displaying it.
 */

const VERDICT_COPY: Record<string, { label: string; tone: string; blurb: string }> = {
  pattern_effective: {
    label: 'PATTERN EFFECTIVE',
    tone: 'effective',
    blurb: 'The interquartile ranges are disjoint, so the arms are separated by the evidence.',
  },
  pattern_harmful: {
    label: 'PATTERN HARMFUL',
    tone: 'harmful',
    blurb:
      'The ranges are disjoint and the pattern arm is the WORSE one. A real result, not a sign error.',
  },
  no_measurable_effect: {
    label: 'NO MEASURABLE EFFECT',
    tone: 'neutral',
    blurb: 'The arms separate but the medians are identical.',
  },
  inconclusive: {
    label: 'INCONCLUSIVE',
    tone: 'inconclusive',
    blurb:
      'The interquartile ranges overlap. No delta is reported, and none should be: a median difference drawn from overlapping distributions is fiction with a decimal point.',
  },
  insufficient_data: {
    label: 'INSUFFICIENT DATA',
    tone: 'inconclusive',
    blurb: 'Fewer than the minimum repetitions per arm. A single pair of chaos runs is noise.',
  },
};

function Box({
  label,
  median,
  iqr,
  max,
  tone,
}: {
  label: string;
  median: number | null;
  iqr: number[] | null;
  max: number;
  tone: string;
}) {
  if (median === null || !iqr || iqr.length < 2) {
    return (
      <div className="cf-row">
        <span className="cf-arm muted">{label}</span>
        <span className="muted" style={{ fontSize: 12 }}>no distribution</span>
      </div>
    );
  }
  const scale = (v: number) => `${Math.max(0, Math.min(100, (v / max) * 100))}%`;
  return (
    <div className="cf-row">
      <span className="cf-arm">{label}</span>
      <div className="cf-track" role="img"
           aria-label={`${label}: median ${median}, interquartile range ${iqr[0]} to ${iqr[1]}`}>
        <div
          className={`cf-box cf-box--${tone}`}
          style={{ left: scale(iqr[0]), width: `calc(${scale(iqr[1])} - ${scale(iqr[0])})` }}
        />
        <div className={`cf-median cf-median--${tone}`} style={{ left: scale(median) }} />
      </div>
      <span className="cf-num">
        {median.toLocaleString(undefined, { maximumFractionDigits: 1 })}
        <span className="muted"> ({iqr[0].toLocaleString(undefined, { maximumFractionDigits: 1 })}
          –{iqr[1].toLocaleString(undefined, { maximumFractionDigits: 1 })})</span>
      </span>
    </div>
  );
}

function PatternCard({ p, metric }: { p: CounterfactualPattern; metric: string }) {
  const copy = VERDICT_COPY[p.verdict] ?? {
    label: p.verdict.toUpperCase(),
    tone: 'neutral',
    blurb: '',
  };
  const max = Math.max(
    ...[p.withIqr?.[1] ?? 0, p.withoutIqr?.[1] ?? 0, p.withMedian ?? 0, p.withoutMedian ?? 0],
    1,
  );

  return (
    <div className="card cf-card" style={{ padding: 16 }}>
      <div className="cf-head">
        <div>
          <strong style={{ fontSize: 13 }}>{p.pattern}</strong>
          <span className="muted" style={{ fontSize: 12, marginLeft: 8 }}>
            {metric} · n={p.n} per arm · interleaved
          </span>
        </div>
        <span className={`cf-verdict cf-verdict--${copy.tone}`}>{copy.label}</span>
      </div>

      <div className="cf-plot">
        <Box label="with" median={p.withMedian} iqr={p.withIqr} max={max} tone="with" />
        <Box label="without" median={p.withoutMedian} iqr={p.withoutIqr} max={max} tone="without" />
      </div>

      {/*
        The delta is rendered ONLY when the API supplied one. There is no
        fallback branch computing it from the two medians — that absence is the
        feature.
      */}
      {p.deltaMedian !== null ? (
        <div className="cf-delta">
          <span className="cf-delta-value">
            {p.deltaMedian.toLocaleString(undefined, { maximumFractionDigits: 1 })}
          </span>
          <span className="muted"> fewer {metric.replace(/_/g, ' ')} per incident (median)</span>
        </div>
      ) : (
        <div className="cf-delta cf-delta--suppressed">
          <span className="cf-delta-value">—</span>
          <span className="muted"> no delta reported</span>
        </div>
      )}

      <p className="muted cf-reason">{p.reason || copy.blurb}</p>

      {p.cost ? (
        <div className="cf-cost">
          <div className="cf-cost-figure">
            ₹{Math.round(p.cost.valuePerIncident).toLocaleString()}{' '}
            <span className="muted">per incident</span>
            {' · '}
            ₹{Math.round(p.cost.valuePerYear).toLocaleString()}{' '}
            <span className="muted">per year</span>
          </div>
          <p className="muted cf-caveat">{p.cost.caveat}</p>
          <ul className="cf-assumptions">
            {p.cost.assumptions.map((a) => (
              <li key={a.key}>
                <span className="cf-tag">{a.tag}</span> {a.label} ={' '}
                <strong>
                  {a.unit === '₹' ? `₹${a.value}` : `${a.value}${a.unit ? ` ${a.unit}` : ''}`}
                </strong>
                <span className="muted"> — {a.basis}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {p.discardedRuns.length > 0 ? (
        <p className="muted cf-discarded">
          {p.discardedRuns.length} run(s) excluded from the distribution (
          {p.discardedRuns.map((d) => d.verdict).join(', ')}) — an unmeasured arm has no number
          worth having.
        </p>
      ) : null}
    </div>
  );
}

export function CounterfactualPanel({ data }: { data: CounterfactualResponse | null }) {
  if (!data || data.patterns.length === 0) {
    return (
      <div className="card" style={{ padding: 16 }}>
        <strong style={{ fontSize: 13 }}>Counterfactual ROI</strong>
        <p className="muted" style={{ margin: '6px 0 0', fontSize: 13 }}>
          No counterfactual pairs recorded yet. Each pattern is run with the fault applied and the
          pattern switched on and off, {data?.minRepetitions ?? 5} times per arm, interleaved.
        </p>
      </div>
    );
  }

  return (
    <section className="cf-panel">
      <div className="cf-panel-head">
        <strong style={{ fontSize: 13 }}>Counterfactual ROI — what each pattern is worth</strong>
        <span className="muted" style={{ fontSize: 12 }}>
          {data.metric} · lower is {data.lowerIsBetter ? 'better' : 'worse'}
        </span>
      </div>
      <p className="muted cf-note">{data.note}</p>
      <div className="cf-grid">
        {data.patterns.map((p) => (
          <PatternCard key={p.pattern} p={p} metric={data.metric} />
        ))}
      </div>
    </section>
  );
}
