import type { ValidityPoint } from '@/types';

/**
 * The validity strip: achieved rps against the declared floor, per run.
 *
 * This is the first thing to show in a demo — "before I show you a pass, here's
 * my framework refusing to score a run it couldn't measure." It makes the
 * project's core discipline visible at a glance rather than buried in a verdict
 * string.
 *
 * INVALID bars are hatched slate, not red: the load generator failed, not the
 * system. Colouring them red would blame the target for the instrument.
 */
export function ValidityStrip({ points }: { points: ValidityPoint[] }) {
  if (points.length === 0) {
    return (
      <div className="card" style={{ padding: 16 }}>
        <strong style={{ fontSize: 13 }}>SLI validity</strong>
        <p className="muted" style={{ margin: '6px 0 0', fontSize: 13 }}>
          No runs recorded yet. Every run reports the traffic it actually achieved against the
          floor its hypothesis declared.
        </p>
      </div>
    );
  }

  const maxRps = Math.max(...points.map((p) => Math.max(p.achievedRps ?? 0, p.floor ?? 0)), 1);
  const invalidCount = points.filter((p) => !p.valid).length;

  return (
    <div className="card" style={{ padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
        <strong style={{ fontSize: 13 }}>SLI validity — achieved rps vs floor</strong>
        <span className="muted" style={{ fontSize: 12 }}>
          {invalidCount === 0
            ? `${points.length} runs, all measurable`
            : `${invalidCount} of ${points.length} unmeasurable`}
        </span>
      </div>

      <div style={{ display: 'flex', gap: 3, alignItems: 'flex-end', height: 64 }}>
        {points.map((p) => {
          const rps = p.achievedRps ?? 0;
          const h = Math.max((rps / maxRps) * 60, 2);
          const floorH = p.floor ? (p.floor / maxRps) * 60 : 0;
          const title = p.valid
            ? `#${p.executionId} · ${rps.toFixed(0)} rps (floor ${p.floor ?? '—'}) · ${p.verdict}`
            : `#${p.executionId} · INVALID · ${p.reason ?? 'below the validity floor'}`;
          return (
            <div key={p.executionId} style={{ position: 'relative', flex: '1 1 0', minWidth: 10, height: 60 }} title={title}>
              <div
                className={p.valid ? undefined : 'invalid-hatch'}
                style={{
                  position: 'absolute', bottom: 0, left: 0, right: 0, height: h,
                  background: p.valid ? 'var(--verdict-held)' : undefined,
                  border: p.valid ? undefined : '1px solid var(--verdict-invalid)',
                  borderRadius: '2px 2px 0 0',
                }}
              />
              {/* The floor is drawn as a line, not implied: a bar below it is
                  not a small bar, it is a run that could not be measured. */}
              {floorH > 0 ? (
                <div
                  style={{
                    position: 'absolute', bottom: floorH, left: -1, right: -1,
                    borderTop: '1px dashed var(--verdict-falsified)',
                  }}
                />
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="muted" style={{ fontSize: 11, marginTop: 8, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <span><span style={{ display: 'inline-block', width: 10, height: 10, background: 'var(--verdict-held)', marginRight: 5, verticalAlign: 'middle' }} />measurable</span>
        <span><span className="invalid-hatch" style={{ display: 'inline-block', width: 10, height: 10, border: '1px solid var(--verdict-invalid)', marginRight: 5, verticalAlign: 'middle' }} />INVALID — no traffic, no evidence</span>
        <span><span style={{ display: 'inline-block', width: 16, borderTop: '1px dashed var(--verdict-falsified)', marginRight: 5, verticalAlign: 'middle' }} />declared floor</span>
      </div>
    </div>
  );
}
