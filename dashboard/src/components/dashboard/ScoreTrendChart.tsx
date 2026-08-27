import type { TrendResponse } from '@/types';

/**
 * The epoch-aware trend chart.
 *
 * A resilience score is only comparable to another score computed the same way.
 * So this chart draws ONE POLYLINE PER EPOCH and never joins them: the API hands
 * back pre-segmented data, and this component has no code path that could
 * interpolate across a boundary even if someone wanted it to.
 *
 * Two honestly separated segments are far more persuasive than one smooth line a
 * reviewer can dismantle. The seam is built in deliberately.
 */

const W = 860;
const H = 220;
const PAD = { top: 16, right: 16, bottom: 30, left: 40 };

export function ScoreTrendChart({ trend }: { trend: TrendResponse }) {
  const all = trend.segments.flatMap((s) => s.points);

  if (all.length === 0) {
    return (
      <div className="card" style={{ padding: 24 }}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>No scoreable runs yet</div>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Only <span className="mono">held</span> and <span className="mono">falsified</span> runs
          carry a comparable score. Runs that were refused, aborted, or could not be measured are
          excluded by design — they appear on the validity strip below instead.
        </p>
      </div>
    );
  }

  const times = all.map((p) => new Date(p.at).getTime());
  const tMin = Math.min(...times);
  const tMax = Math.max(...times);
  const span = Math.max(tMax - tMin, 1);

  const x = (iso: string) =>
    PAD.left + ((new Date(iso).getTime() - tMin) / span) * (W - PAD.left - PAD.right);
  const y = (score: number) => PAD.top + (1 - score) * (H - PAD.top - PAD.bottom);

  return (
    <div className="card" style={{ padding: 16, overflowX: 'auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <strong style={{ fontSize: 13 }}>Resilience score</strong>
        <span className="muted" style={{ fontSize: 12 }}>
          {trend.segments.length} epoch{trend.segments.length === 1 ? '' : 's'} ·{' '}
          {all.length} scoreable · {trend.excluded.length} excluded
        </span>
      </div>

      <svg width={W} height={H} role="img" aria-label="Resilience score over time, segmented by scoring epoch">
        {[0, 0.25, 0.5, 0.75, 1].map((g) => (
          <g key={g}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(g)} y2={y(g)} stroke="var(--border)" strokeWidth={1} />
            <text x={PAD.left - 8} y={y(g) + 4} textAnchor="end" fontSize={10} fill="var(--text-muted)" className="mono">
              {g.toFixed(2)}
            </text>
          </g>
        ))}

        {/* Epoch boundaries: labelled vertical lines carrying the change reason.
            The seam is the point — it says "these two stretches are not
            directly comparable, and here is why". */}
        {trend.boundaries.map((b) => (
          <g key={`b-${b.epochId}`}>
            <line
              x1={x(b.at)} x2={x(b.at)} y1={PAD.top} y2={H - PAD.bottom}
              stroke="var(--epoch-boundary)" strokeWidth={1.5} strokeDasharray="4 3"
            />
            <text
              x={x(b.at) + 4} y={PAD.top + 10}
              fontSize={10} fill="var(--epoch-boundary)"
            >
              epoch {b.epochId} · {b.changeReason.slice(0, 46)}
            </text>
          </g>
        ))}

        {/* ONE polyline per epoch. There is deliberately no path that joins two
            segments — the data arrives separated and is never flattened. */}
        {trend.segments.map((seg) => {
          const pts = seg.points.map((p) => `${x(p.at)},${y(p.score)}`).join(' ');
          return (
            <g key={`seg-${seg.epochId}`}>
              {seg.points.length > 1 ? (
                <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth={2} />
              ) : null}
              {seg.points.map((p) =>
                p.retroScored ? (
                  // Hollow: re-derived under a later epoch, not measured under it.
                  <circle
                    key={p.executionId} cx={x(p.at)} cy={y(p.score)} r={4}
                    fill="var(--bg)" stroke="var(--accent)" strokeWidth={2}
                  >
                    <title>#{p.executionId} · {p.score.toFixed(3)} · retro-scored under epoch {p.epochId}</title>
                  </circle>
                ) : (
                  <circle key={p.executionId} cx={x(p.at)} cy={y(p.score)} r={3.5} fill="var(--accent)">
                    <title>#{p.executionId} · {p.score.toFixed(3)} · epoch {p.epochId}</title>
                  </circle>
                ),
              )}
            </g>
          );
        })}
      </svg>

      <div className="muted" style={{ fontSize: 11, marginTop: 6, display: 'flex', gap: 18, flexWrap: 'wrap' }}>
        <span>
          <span style={{ display: 'inline-block', width: 18, borderTop: '1.5px dashed var(--epoch-boundary)', marginRight: 5, verticalAlign: 'middle' }} />
          epoch boundary — the line breaks here; points either side are not directly comparable
        </span>
        <span>◦ hollow = retro-scored</span>
      </div>
    </div>
  );
}
