import './globals.css';
import type { Metadata } from 'next';
import { READ_ONLY } from '@/lib/api';

export const metadata: Metadata = {
  title: 'ChaosProof',
  description: 'Chaos experiments with a verification layer that refuses to answer when it could not measure.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header
          style={{
            borderBottom: '1px solid var(--border)',
            padding: '14px 24px',
            display: 'flex',
            alignItems: 'baseline',
            gap: 16,
          }}
        >
          <strong style={{ fontSize: 15 }}>ChaosProof</strong>
          <span className="muted" style={{ fontSize: 12 }}>
            Don&apos;t hope your system is resilient — prove it, and refuse to answer when you can&apos;t measure.
          </span>
          {READ_ONLY ? (
            <span
              className="mono muted"
              style={{
                marginLeft: 'auto', fontSize: 11, border: '1px solid var(--border)',
                borderRadius: 4, padding: '2px 8px',
              }}
              title="Trigger controls are absent from this build, not merely disabled."
            >
              READ-ONLY BUILD
            </span>
          ) : null}
        </header>
        <main style={{ padding: 24, maxWidth: 1180, margin: '0 auto' }}>{children}</main>
      </body>
    </html>
  );
}
