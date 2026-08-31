import { CounterfactualPanel } from '@/components/dashboard/CounterfactualPanel';
import type { CounterfactualResponse } from '@/types';

/**
 * GATE 10's visual, on FIXTURE data, using the same component the live panel
 * uses. Banner-labelled, and the banner is not decoration.
 *
 * WHY THIS PAGE EXISTS, stated plainly rather than implied: the live counter-
 * factual for `paymentService.fallback` could not be completed. Removing the
 * fallback does not degrade the system gently — it makes the CIRCUIT BREAKER's
 * opening catastrophic, because an open breaker with nothing to fall back to
 * converts a small error rate into total failure for its 30s wait. Client
 * availability collapsed past the "without" arm's tightened abort threshold on
 * every attempt (measured at 0.7427, 0.7928, 0.8137, 0.8311 against a 0.8500
 * floor, across three separate fault severities), so the safety plane stopped
 * the measurement before it could be taken.
 *
 * That is a real finding, and the framework reported it correctly as
 * INSUFFICIENT_DATA rather than computing a delta from a full `with` arm and an
 * empty `without` one. The live panel on `/` shows exactly that.
 *
 * What this page adds is proof that the RENDERER distinguishes the two states
 * the gate asks for — a separated pair with a delta, and an overlapping pair
 * with none — side by side. Inventing a delta on the live panel to make a
 * screenshot look better would be the exact failure the whole feature is
 * built to prevent, so the fixture is labelled instead.
 */
const FIXTURE: CounterfactualResponse = {
  metric: 'failed_requests',
  lowerIsBetter: true,
  minRepetitions: 5,
  note:
    'FIXTURE DATA — shaped to exercise both render paths. The live panel on the home page shows the real result.',
  patterns: [
    {
      pattern: 'paymentService.fallback',
      metric: 'failed_requests',
      n: 5,
      withMedian: 131,
      withIqr: [125, 138],
      withoutMedian: 5310,
      withoutIqr: [5250, 5380],
      overlap: false,
      verdict: 'pattern_effective',
      reason: 'IQRs are disjoint and the pattern arm is better (131 vs 5310)',
      deltaMedian: 5179,
      lowerIsBetter: true,
      pairId: 'fixture-effective',
      experiment: 'counterfactual_partial_loss_fallback',
      discardedRuns: [],
      withValues: [120, 140, 131, 125, 138],
      withoutValues: [5200, 5400, 5310, 5250, 5380],
      cost: {
        pattern: 'paymentService.fallback',
        requestsSavedPerIncident: 5179,
        valuePerIncident: 2330550,
        valuePerYear: 27966600,
        currency: 'INR',
        caveat:
          'n=5 per arm. This figure is the median delta (5179 requests) priced with three assumptions shown alongside it; change any of them and the figure moves.',
        assumptions: [
          {
            key: 'revenue_per_checkout',
            value: 450,
            label: 'revenue per successful checkout',
            basis: 'median order value, order-api sample data',
            unit: '₹',
            editable: true,
            tag: '[assumption]',
          },
          {
            key: 'conversion_loss_per_failed_request',
            value: 1,
            label: 'conversion loss per failed request',
            basis:
              'pessimistic: assumes every failed checkout is a lost sale rather than a retry by the user',
            unit: 'fraction',
            editable: true,
            tag: '[assumption]',
          },
          {
            key: 'incidents_per_year',
            value: 12,
            label: 'incidents of this class per year',
            basis:
              'extrapolated from 6 months of incident data — the weakest of the three, and the one most worth arguing about',
            unit: 'count',
            editable: true,
            tag: '[assumption]',
          },
        ],
      },
    },
    {
      pattern: 'paymentService.fallback (pod-kill fault)',
      metric: 'failed_requests',
      n: 5,
      withMedian: 310,
      withIqr: [290, 330],
      withoutMedian: 300,
      withoutIqr: [295, 340],
      overlap: true,
      verdict: 'inconclusive',
      reason:
        'IQRs overlap (with 290-330, without 295-340) — the arms are not separated by this evidence. No delta is reported, because a median delta drawn from overlapping distributions is fiction with a decimal point.',
      deltaMedian: null,
      lowerIsBetter: true,
      pairId: 'fixture-inconclusive',
      experiment: 'counterfactual_pod_kill_fallback',
      discardedRuns: [],
      withValues: [310, 290, 330, 275, 345],
      withoutValues: [300, 340, 285, 360, 295],
      cost: null,
    },
  ],
};

export default function CounterfactualFixturePage() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div
        className="card"
        style={{
          padding: 14,
          borderColor: 'var(--verdict-invalid)',
          background: 'var(--verdict-invalid-bg)',
        }}
      >
        <strong style={{ fontSize: 13 }}>FIXTURE DATA — not a measurement</strong>
        <p className="muted" style={{ margin: '6px 0 0', fontSize: 12, lineHeight: 1.5 }}>
          Both cards below are constructed to exercise the two render paths GATE 10 asks for: a
          separated pair that reports a delta, and an overlapping pair that reports{' '}
          <code>inconclusive</code> and no delta figure. They are rendered by the same component the
          live panel uses.
        </p>
        <p className="muted" style={{ margin: '8px 0 0', fontSize: 12, lineHeight: 1.5 }}>
          The live counterfactual could not be completed, and that is a result rather than a gap:
          removing the queued fallback makes the circuit breaker&apos;s opening catastrophic — an
          open breaker with nothing to fall back to turns a small error rate into total failure for
          its 30s wait. The &quot;without&quot; arm&apos;s availability collapsed past its tightened
          abort threshold on every attempt (0.7427, 0.7928, 0.8137, 0.8311 against a 0.8500 floor,
          across three fault severities), so the safety plane stopped the measurement before it
          could be taken. The framework reported <code>INSUFFICIENT_DATA</code>; the home page shows
          that real result.
        </p>
      </div>

      <CounterfactualPanel data={FIXTURE} />
    </div>
  );
}
