import { ScoreTrendChart } from '@/components/dashboard/ScoreTrendChart';
import { ValidityStrip } from '@/components/dashboard/ValidityStrip';
import { CounterfactualPanel } from '@/components/dashboard/CounterfactualPanel';
import { VerdictBadge, toVerdict } from '@/components/common/VerdictBadge';
import { fetchJson } from '@/lib/api';
import type { CounterfactualResponse, TrendResponse, ValidityPoint } from '@/types';

interface ScoreResponse {
  score: number | null;
  weightsDenominator: number | null;
  experiment: string;
  executionId: number;
  at: string | null;
  epoch: { id: number; sha256: string; changeReason: string } | null;
  epochAggregate: { mean: number | null; n: number };
  reason?: string;
}

interface ExecutionRow {
  id: number;
  experiment: string;
  verdictKind: string;
  verdictReason: string | null;
  score: number | null;
  weightsDenominator: number | null;
  epochId: number | null;
  epochSha: string | null;
  finishedAt: string | null;
}

export const dynamic = 'force-dynamic';

function ErrorPanel({ what, message }: { what: string; message: string }) {
  return (
    <div className="card" style={{ padding: 16, borderColor: 'var(--verdict-error)' }}>
      <strong style={{ fontSize: 13, color: 'var(--verdict-error)' }}>{what} unavailable</strong>
      <p className="muted" style={{ margin: '6px 0 0', fontSize: 12 }}>{message}</p>
      <p className="muted" style={{ margin: '6px 0 0', fontSize: 12 }}>
        Start the framework API with{' '}
        <span className="mono">python -m uvicorn src.api:app --port 8000</span> from{' '}
        <span className="mono">chaos-framework/</span>.
      </p>
    </div>
  );
}

export default async function Page() {
  const [score, trend, validity, executions, counterfactual] = await Promise.all([
    fetchJson<ScoreResponse>('/api/score'),
    fetchJson<TrendResponse>('/api/trends'),
    fetchJson<ValidityPoint[]>('/api/validity'),
    fetchJson<ExecutionRow[]>('/api/executions?limit=12'),
    fetchJson<CounterfactualResponse>('/api/counterfactual'),
  ]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* A score is NEVER rendered without its epoch. A bare number invites
          exactly the comparison that scoring epochs exist to prevent. */}
      {score.error ? (
        <ErrorPanel what="Score" message={score.error} />
      ) : score.data?.score == null ? (
        <div className="card" style={{ padding: 20 }}>
          <strong>No scoreable runs yet</strong>
          <p className="muted" style={{ margin: '6px 0 0', fontSize: 13 }}>
            {score.data?.reason ?? 'Run one with '}
            <span className="mono">make experiment NAME=pod_kill_payment_svc</span>.
          </p>
        </div>
      ) : (
        <div className="card" style={{ padding: 20, display: 'flex', gap: 32, flexWrap: 'wrap', alignItems: 'baseline' }}>
          <div>
            <div className="muted" style={{ fontSize: 11, letterSpacing: '0.08em' }}>LATEST SCORE</div>
            <div className="mono" style={{ fontSize: 40, lineHeight: 1.1 }}>{score.data.score.toFixed(3)}</div>
            <div className="muted" style={{ fontSize: 11 }}>
              denominator {score.data.weightsDenominator?.toFixed(3) ?? '—'} · {score.data.experiment}
            </div>
          </div>
          <div>
            <div className="muted" style={{ fontSize: 11, letterSpacing: '0.08em' }}>EPOCH MEAN</div>
            <div className="mono" style={{ fontSize: 40, lineHeight: 1.1 }}>
              {score.data.epochAggregate.mean != null ? score.data.epochAggregate.mean.toFixed(3) : '—'}
            </div>
            <div className="muted" style={{ fontSize: 11 }}>
              over {score.data.epochAggregate.n} scoreable run{score.data.epochAggregate.n === 1 ? '' : 's'}
            </div>
          </div>
          <div style={{ minWidth: 240 }}>
            <div className="muted" style={{ fontSize: 11, letterSpacing: '0.08em' }}>SCORING EPOCH</div>
            <div className="mono" style={{ fontSize: 13 }}>
              #{score.data.epoch?.id} · {score.data.epoch?.sha256.slice(0, 12)}
            </div>
            <div className="muted" style={{ fontSize: 11 }}>{score.data.epoch?.changeReason}</div>
          </div>
        </div>
      )}

      {trend.error ? <ErrorPanel what="Trend" message={trend.error} /> : <ScoreTrendChart trend={trend.data!} />}

      {validity.error ? (
        <ErrorPanel what="Validity strip" message={validity.error} />
      ) : (
        <ValidityStrip points={validity.data!} />
      )}

      {/* The ROI panel. Counterfactual executions are excluded from the score
          and the trend above — they are deliberately degraded configurations,
          and averaging them in would make the system look worse the more
          carefully it is measured. */}
      {counterfactual.error ? (
        <ErrorPanel what="Counterfactual ROI" message={counterfactual.error} />
      ) : (
        <CounterfactualPanel data={counterfactual.data} />
      )}

      <div className="card" style={{ padding: 16 }}>
        <strong style={{ fontSize: 13 }}>Recent executions</strong>
        {executions.error ? (
          <p className="muted" style={{ fontSize: 12 }}>{executions.error}</p>
        ) : executions.data!.length === 0 ? (
          <p className="muted" style={{ margin: '6px 0 0', fontSize: 13 }}>
            Nothing recorded yet — this is the normal state of a fresh install.
          </p>
        ) : (
          <div style={{ overflowX: 'auto', marginTop: 10 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr className="muted" style={{ textAlign: 'left', fontSize: 10, letterSpacing: '0.08em' }}>
                  <th style={{ padding: '6px 8px' }}>#</th>
                  <th style={{ padding: '6px 8px' }}>EXPERIMENT</th>
                  <th style={{ padding: '6px 8px' }}>VERDICT</th>
                  <th style={{ padding: '6px 8px' }}>SCORE</th>
                  <th style={{ padding: '6px 8px' }}>EPOCH</th>
                </tr>
              </thead>
              <tbody>
                {executions.data!.map((e) => (
                  <tr key={e.id} style={{ borderTop: '1px solid var(--border)' }}>
                    <td className="mono" style={{ padding: '7px 8px' }}>{e.id}</td>
                    <td className="mono" style={{ padding: '7px 8px' }}>{e.experiment}</td>
                    <td style={{ padding: '7px 8px' }}>
                      <VerdictBadge verdict={toVerdict(e.verdictKind, e.verdictReason)} showDetail={false} />
                    </td>
                    {/* An unscoreable run shows an em dash, never 0.000 — a zero
                        would read as "the system failed" when nothing was scored. */}
                    <td className="mono" style={{ padding: '7px 8px' }}>
                      {e.score != null ? e.score.toFixed(3) : <span className="muted">—</span>}
                    </td>
                    <td className="mono muted" style={{ padding: '7px 8px' }}>
                      {e.epochId != null ? `#${e.epochId}` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
