/**
 * The stub the read-only build resolves instead of `TriggerPanel.tsx`.
 *
 * It renders nothing and it says nothing. There is no "controls unavailable"
 * notice and no disabled button, because a disabled control is an advertisement
 * that a control exists — and a control that 404s invites someone to find out
 * why. The public build should look like a dashboard that was never intended to
 * trigger anything.
 *
 * The type is re-exported so the call site type-checks identically under both
 * builds; a stub that changed the signature would turn a build-flag change into
 * a compile error at the worst possible moment.
 */

export interface TriggerTarget {
  name: string;
  gating: boolean;
  blastScore: number | null;
  namespace: string;
}

export function TriggerPanel(_props: { targets: TriggerTarget[] }) {
  return null;
}
