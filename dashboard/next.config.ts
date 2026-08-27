import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // READ_ONLY is a BUILD-TIME flag, not a runtime toggle. Trigger controls are
  // absent from the public build rather than disabled in it: a control that
  // 404s invites someone to find out why.
  env: { READ_ONLY: process.env.READ_ONLY ?? 'true' },
};

export default nextConfig;
