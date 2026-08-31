'use client';

import { useState } from 'react';

/**
 * The operator trigger surface — INTERNAL BUILD ONLY.
 *
 * `next.config.ts` aliases this whole module to `TriggerPanel.readonly.tsx` when
 * READ_ONLY is set, so none of the strings below reach the public bundle. The
 * marker constant exists so `verify:readonly` can assert that against the built
 * output instead of trusting the config.
 *
 * WHY THIS RENDERS A COMMAND RATHER THAN POSTING A REQUEST
 *
 * There is deliberately no trigger endpoint on the framework API — the note
 * sits in `api.py` beside the read routes. No HTTP path reachable from a
 * browser can inject a fault; the only thing that can is a process inside the
 * cluster holding a service account scoped for it. Adding a POST route here so
 * that a button could feel more like a button would hand a browser session the
 * ability to start a blast-radius-61 experiment, and would do it in the one
 * component most likely to end up on a public URL by accident.
 *
 * So the control composes the exact invocation, shows the blast radius and
 * gating status of what is about to run, and hands the operator a command. The
 * authorisation is a person with cluster credentials — not a click.
 *
 * It is still a trigger control, and it is still absent from the public build:
 * it enumerates the internal experiment set, names the override flag that
 * satisfies a `require_override` policy rule, and is a runbook for taking the
 * cluster down. None of that belongs on a demo page.
 */

export const TRIGGER_SURFACE_MARKER = 'chaosproof-internal-trigger-surface';

export interface TriggerTarget {
  name: string;
  gating: boolean;
  blastScore: number | null;
  namespace: string;
}

export function TriggerPanel({ targets }: { targets: TriggerTarget[] }) {
  const [selected, setSelected] = useState(targets[0]?.name ?? '');
  const [override, setOverride] = useState(false);
  const [copied, setCopied] = useState(false);

  const target = targets.find((t) => t.name === selected);
  const command =
    `python -m src.chaosctl run ${selected}` + (override ? ' --override' : '');

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // A clipboard permission denial must not blank the panel — the command is
      // rendered as selectable text above regardless.
      setCopied(false);
    }
  }

  return (
    <div
      className="card"
      data-testid={TRIGGER_SURFACE_MARKER}
      style={{ padding: 16, borderColor: 'var(--verdict-falsified)' }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <strong style={{ fontSize: 13 }}>Trigger an experiment</strong>
        <span className="mono muted" style={{ fontSize: 10, letterSpacing: '0.08em' }}>
          INTERNAL BUILD
        </span>
      </div>

      <p className="muted" style={{ margin: '6px 0 12px', fontSize: 12 }}>
        ChaosProof has no HTTP path to fault injection, by design. This composes
        the invocation; you run it with cluster credentials.
      </p>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <label style={{ fontSize: 12 }}>
          <span className="muted" style={{ marginRight: 6 }}>Experiment</span>
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            className="mono"
            style={{ fontSize: 12, padding: '4px 6px' }}
          >
            {targets.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>
        </label>

        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input
            type="checkbox"
            checked={override}
            onChange={(e) => setOverride(e.target.checked)}
          />
          <span className="muted">
            <span className="mono">--override</span> (satisfies a{' '}
            <span className="mono">require_override</span> policy rule; recorded)
          </span>
        </label>
      </div>

      {/* Blast radius and gating status are shown BEFORE the command, not after.
          A trigger control that does not say what it is about to affect is the
          kind of control that gets used at 02:00 by someone who assumed. */}
      {target ? (
        <p className="muted" style={{ margin: '10px 0 0', fontSize: 12 }}>
          namespace <span className="mono">{target.namespace}</span> · blast score{' '}
          <span className="mono">{target.blastScore ?? '—'}</span> ·{' '}
          {target.gating ? 'GATING' : 'advisory — cannot block a merge'}
        </p>
      ) : null}

      <div
        className="mono"
        style={{
          marginTop: 10, padding: '8px 10px', fontSize: 12,
          border: '1px solid var(--border)', borderRadius: 4,
          overflowX: 'auto', whiteSpace: 'pre',
        }}
      >
        {command}
      </div>

      <button
        onClick={copy}
        style={{
          marginTop: 8, fontSize: 12, padding: '4px 10px',
          border: '1px solid var(--border)', borderRadius: 4,
          background: 'transparent', color: 'inherit', cursor: 'pointer',
        }}
      >
        {copied ? 'copied' : 'copy command'}
      </button>
    </div>
  );
}
