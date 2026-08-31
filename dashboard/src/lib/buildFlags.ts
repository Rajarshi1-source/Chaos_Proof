/**
 * BUILD-TIME flags, not runtime toggles.
 *
 * `READ_ONLY` is inlined by `next.config.ts` at build time, so
 * `process.env.READ_ONLY !== 'false'` constant-folds to a literal and every
 * branch guarded by it is eliminated. That is necessary but NOT sufficient: a
 * dropped branch still leaves the imported module in the graph unless
 * tree-shaking gets it too, and "unless tree-shaking gets it" is not a security
 * property.
 *
 * So the flag is enforced twice. This constant removes the JSX, and
 * `next.config.ts` aliases the trigger module itself to a stub so the real one
 * is never resolved by either bundler. `npm run verify:readonly` then asserts
 * against the built output rather than against the intent — and asserts BOTH
 * directions, because a verifier that can only pass proves nothing.
 */
export const READ_ONLY = process.env.READ_ONLY !== 'false';
