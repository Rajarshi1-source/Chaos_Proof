import type { Verdict, VerdictKind } from '@/types';

/**
 * The badge is driven by the discriminated union, and the switch below is
 * EXHAUSTIVE. `assertNever` in the default arm means adding a seventh verdict
 * without deciding how it should read is a compile error — not a silently grey
 * badge that ships and then has to be explained.
 */
function assertNever(x: never): never {
  throw new Error(`unhandled verdict: ${JSON.stringify(x)}`);
}

interface Style {
  label: string;
  fg: string;
  bg: string;
  /** Hatched fill: reserved for INVALID, which is neither pass nor fail. */
  hatched?: boolean;
  /** Shown inline — a verdict without its reason is not actionable. */
  detail?: string;
}

export function verdictStyle(v: Verdict): Style {
  switch (v.kind) {
    case 'held':
      return { label: 'HYPOTHESIS_HELD', fg: 'var(--verdict-held)', bg: 'var(--verdict-held-bg)' };
    case 'falsified':
      return {
        label: 'HYPOTHESIS_FALSIFIED',
        fg: 'var(--verdict-falsified)',
        bg: 'var(--verdict-falsified-bg)',
        // "The experiment failed" is not actionable; naming the invariant is.
        detail: v.falsifiedInvariants.join(', '),
      };
    case 'invalid':
      return {
        label: 'INVALID',
        fg: 'var(--verdict-invalid)',
        bg: 'var(--verdict-invalid-bg)',
        hatched: true,
        detail: v.reason,
      };
    case 'skipped':
      // A refusal is correct behaviour, so it is neutral — never red.
      return { label: 'SKIPPED', fg: 'var(--verdict-skipped)', bg: 'var(--verdict-skipped-bg)', detail: v.reason };
    case 'denied':
      return { label: 'DENIED', fg: 'var(--verdict-denied)', bg: 'var(--verdict-denied-bg)', detail: v.rule };
    case 'aborted':
      return { label: 'ABORTED', fg: 'var(--verdict-aborted)', bg: 'var(--verdict-aborted-bg)', detail: v.reason };
    case 'error':
      return { label: 'ERROR', fg: 'var(--verdict-error)', bg: 'var(--verdict-error-bg)', detail: v.message };
    default:
      return assertNever(v);
  }
}

/** Colour only, for chart strokes and timeline markers. Same source of truth. */
export function verdictColor(kind: VerdictKind): string {
  return `var(--verdict-${kind})`;
}

export function VerdictBadge({ verdict, showDetail = true }: { verdict: Verdict; showDetail?: boolean }) {
  const s = verdictStyle(verdict);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
      <span
        className={s.hatched ? 'invalid-hatch mono' : 'mono'}
        style={{
          color: s.fg,
          background: s.hatched ? undefined : s.bg,
          border: `1px solid ${s.fg}`,
          borderRadius: 4,
          padding: '2px 8px',
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '0.06em',
          whiteSpace: 'nowrap',
        }}
      >
        {s.label}
      </span>
      {showDetail && s.detail ? (
        <span className="muted" style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {s.detail}
        </span>
      ) : null}
    </span>
  );
}

/** Build a Verdict from the API's flat shape, keeping the union authoritative. */
export function toVerdict(kind: string, reason: string | null, invariants: string[] = []): Verdict {
  switch (kind) {
    case 'held':      return { kind: 'held' };
    case 'falsified': return { kind: 'falsified', falsifiedInvariants: invariants };
    case 'invalid':   return { kind: 'invalid', reason: reason ?? 'could not measure' };
    case 'skipped':   return { kind: 'skipped', reason: reason ?? 'pre-flight refused' };
    case 'denied':    return { kind: 'denied', rule: reason ?? 'policy refused' };
    case 'aborted':   return { kind: 'aborted', reason: reason ?? 'halted mid-fault' };
    default:          return { kind: 'error', message: reason ?? `unknown verdict ${kind}` };
  }
}
