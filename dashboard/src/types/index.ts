/**
 * The verdict vocabulary drives the type system.
 *
 * This is a discriminated union, not a string, so `VerdictBadge`'s switch is
 * exhaustive: adding a seventh verdict becomes a COMPILE ERROR rather than a
 * silently grey badge that nobody notices until an interviewer asks what it means.
 */
export type Verdict =
  | { kind: 'held' }
  | { kind: 'falsified'; falsifiedInvariants: string[] }
  | { kind: 'invalid'; reason: string }   // could not measure — NOT a failure
  | { kind: 'skipped'; reason: string }   // pre-flight refused
  | { kind: 'denied'; rule: string }      // policy refused
  | { kind: 'aborted'; reason: string }   // halted mid-fault by a guard
  | { kind: 'error'; message: string };

export type VerdictKind = Verdict['kind'];

/**
 * Verdicts that carry a comparable score. Everything else is excluded from the
 * trend and from every aggregate — INVALID never contributes, not even as zero,
 * because a zero would read as "the system failed" when nothing was measured.
 */
export const SCOREABLE: readonly VerdictKind[] = ['held', 'falsified'] as const;

export function isScoreable(kind: VerdictKind): boolean {
  return SCOREABLE.includes(kind);
}

export interface InvariantOutcome {
  name: string;
  outcome: 'held' | 'falsified' | 'invalid';
  worstValue: number | null;
  threshold: number;
  breachedForS: number;
  kind: 'hold_throughout' | 'recovery' | '-';
  evidence: Record<string, unknown>;
}

export interface CheckResult {
  checkType: string;
  checkName: string;
  applicable: boolean;
  outcome: 'pass' | 'fail' | 'partial' | 'invalid';
  score: number | null;
  expectedValue: string | null;
  actualValue: string | null;
  message: string;
}

export interface Epoch {
  id: number;
  sha256: string;
  weights: Record<string, number>;
  experimentSet: string[];
  sloVersion: number;
  scorerVersion: string;
  startedAt: string;
  endedAt: string | null;
  changeReason: string;
}

export interface LoadRun {
  tool: string;
  toolVersion: string;
  workloadModel: 'open' | 'closed';
  targetRps: number;
  achievedRps: number | null;
  droppedIterations: number;
  clientErrorRate: number | null;
  clientP99Ms: number | null;
  coverage: number | null;
  invalidityReason: string | null;
}

export interface Execution {
  id: number;
  experiment: string;
  verdict: Verdict;
  /** null whenever the verdict is not scoreable. Never coerce this to 0. */
  score: number | null;
  /** The renormalisation denominator, persisted so a score can be re-derived. */
  weightsDenominator: number | null;
  /** A score is meaningless without the epoch it was computed under. */
  epochId: number | null;
  epochSha: string | null;
  /** True when this row was re-derived under a later epoch rather than measured. */
  retroScored: boolean;
  /** Counterfactual runs are deliberately degraded and never enter the trend. */
  counterfactual: boolean;
  gitSha: string | null;
  startedAt: string;
  faultInjectedAt: string | null;
  finishedAt: string | null;
  load: LoadRun | null;
  minRpsFloor: number | null;
  invariants: InvariantOutcome[];
  checks: CheckResult[];
  blastRadius: Record<string, unknown> | null;
}

export interface TrendPoint {
  executionId: number;
  at: string;
  score: number;
  epochId: number;
  retroScored: boolean;
}

/** One unbroken run of points within a single epoch. Segments never join. */
export interface TrendSegment {
  epochId: number;
  epochSha: string;
  changeReason: string;
  points: TrendPoint[];
}

export interface TrendResponse {
  segments: TrendSegment[];
  /** Rendered as labelled vertical lines carrying the change reason. */
  boundaries: { epochId: number; at: string; changeReason: string }[];
  excluded: { executionId: number; at: string; verdict: VerdictKind }[];
}

export interface ValidityPoint {
  executionId: number;
  at: string;
  achievedRps: number | null;
  floor: number | null;
  verdict: VerdictKind;
  valid: boolean;
  reason: string | null;
}

/** Every data view renders all four of these; none of them is hypothetical. */
export type ViewState<T> =
  | { state: 'loading' }
  | { state: 'empty'; hint: string }
  | { state: 'error'; message: string; stale?: T; staleAgeS?: number }
  | { state: 'ready'; data: T; staleAgeS?: number };
