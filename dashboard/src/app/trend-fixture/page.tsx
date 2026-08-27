import { ScoreTrendChart } from '@/components/dashboard/ScoreTrendChart';
import fixture from '@/lib/trend.fixture.json';
import type { TrendResponse } from '@/types';

/**
 * A rendering fixture, labelled as one.
 *
 * Live data currently contains a single scoring epoch, so the real trend draws
 * no boundary. Rather than insert a second epoch row to make a screenshot look
 * better — which would be precisely the dishonesty this project exists to
 * prevent — this route feeds the SAME chart component deterministic two-epoch
 * data so the boundary behaviour can be seen and checked.
 *
 * The banner is not decoration: an unlabelled demo page eventually gets
 * screenshotted as if it were real.
 */
export const dynamic = 'force-static';

export default function TrendFixturePage() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div
        className="card"
        style={{ padding: 14, borderColor: 'var(--verdict-falsified)' }}
      >
        <strong style={{ fontSize: 13, color: 'var(--verdict-falsified)' }}>
          FIXTURE DATA — not a real run
        </strong>
        <p className="muted" style={{ margin: '6px 0 0', fontSize: 13 }}>
          Deterministic two-epoch data, rendered through the same{' '}
          <span className="mono">ScoreTrendChart</span> the dashboard uses. It exists to make the
          epoch-boundary behaviour checkable while live history still contains a single epoch.
          Epoch 2 opens for real when the weights, the SLO version, the scorer version, or the
          gating experiment set changes.
        </p>
      </div>

      <ScoreTrendChart trend={fixture as TrendResponse} />

      <div className="card" style={{ padding: 14 }}>
        <strong style={{ fontSize: 13 }}>What to look for</strong>
        <ul className="muted" style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 13 }}>
          <li>
            The line <em>breaks</em> at the boundary. The last point of epoch 1 and the first of
            epoch 2 are never joined — scores computed under different weights are not comparable,
            and a continuous line would assert that they are.
          </li>
          <li>
            The boundary is a labelled vertical line carrying its{' '}
            <span className="mono">change_reason</span>, so a viewer can see <em>why</em> the two
            stretches differ rather than guessing.
          </li>
          <li>
            Retro-scored points render hollow: history re-derived under the current epoch, not
            measured under it.
          </li>
        </ul>
      </div>
    </div>
  );
}
