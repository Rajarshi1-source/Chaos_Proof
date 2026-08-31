import path from 'node:path';
import type { NextConfig } from 'next';

// READ_ONLY is a BUILD-TIME flag, not a runtime toggle. Trigger controls are
// ABSENT from the public build rather than disabled in it: a control that 404s
// invites someone to find out why, and a disabled button is an advertisement
// that a control exists.
//
// Two mechanisms, because one is not enough:
//
//   1. `env` inlines the flag, so `READ_ONLY ? null : <TriggerPanel/>`
//      constant-folds and the JSX branch is eliminated.
//   2. The module itself is ALIASED to a stub, so the real component is never
//      resolved by either bundler. Relying on (1) alone leaves the import in the
//      graph unless tree-shaking also removes it, and "unless tree-shaking gets
//      it" is not a property worth deploying on.
//
// `npm run verify:readonly` asserts this against the built output, in both
// directions — a verifier that can only pass proves nothing.
const READ_ONLY = process.env.READ_ONLY !== 'false';

const TRIGGER_MODULE = path.resolve('./src/components/controls/TriggerPanel.tsx');
const TRIGGER_STUB = path.resolve('./src/components/controls/TriggerPanel.readonly.tsx');

// Turbopack resolves aliases as module SPECIFIERS relative to the project root,
// and rejects a Windows absolute path outright — "windows imports are not
// implemented yet", which surfaces as a module-not-found on the import site
// rather than on the alias. Webpack wants the opposite: a real resolved path.
// Hence two spellings of the same target rather than one shared constant.
const TRIGGER_STUB_SPECIFIER = './src/components/controls/TriggerPanel.readonly.tsx';

const nextConfig: NextConfig = {
  env: { READ_ONLY: READ_ONLY ? 'true' : 'false' },

  // Turbopack is the default bundler for Next 16 builds.
  turbopack: {
    resolveAlias: READ_ONLY
      ? { '@/components/controls/TriggerPanel': TRIGGER_STUB_SPECIFIER }
      : {},
  },

  // And the webpack path, for `next build --webpack` and for any tooling that
  // still resolves through it. Declaring only one of the two would make the
  // guarantee depend on which bundler happened to run.
  webpack(config) {
    if (READ_ONLY) {
      config.resolve = config.resolve ?? {};
      config.resolve.alias = {
        ...(config.resolve.alias ?? {}),
        [TRIGGER_MODULE]: TRIGGER_STUB,
        '@/components/controls/TriggerPanel': TRIGGER_STUB,
      };
    }
    return config;
  },
};

export default nextConfig;
