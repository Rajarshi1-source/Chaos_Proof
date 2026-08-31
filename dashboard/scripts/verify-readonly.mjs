#!/usr/bin/env node
/**
 * Assert that the built output contains no trigger surface.
 *
 * This checks the ARTEFACT, not the intent. `next.config.ts` says the module is
 * aliased away and `buildFlags.ts` says the branch folds; both are claims about
 * what should happen, and neither survives a bundler upgrade on its own. The
 * only thing worth deploying on is a grep over what was actually emitted.
 *
 * IT ASSERTS BOTH DIRECTIONS. A verifier that can only pass proves nothing: if
 * the marker were misspelled, or the build directory wrong, or the output not
 * produced at all, a one-directional check would report success on an empty
 * search. So:
 *
 *     --expect absent   the marker must NOT appear  (public build)
 *     --expect present  the marker MUST appear      (internal build)
 *
 * Run both in CI. The second is what makes the first mean something.
 *
 *     READ_ONLY=false npm run build && node scripts/verify-readonly.mjs --expect present
 *     READ_ONLY=true  npm run build && node scripts/verify-readonly.mjs --expect absent
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const MARKER = 'chaosproof-internal-trigger-surface';

// Strings that only exist inside the trigger surface. The marker alone is a
// single point of failure — a refactor that renames it would silently turn this
// check into a no-op that always passes.
const CORROBORATING = ['Trigger an experiment', 'copy command'];

const BUILD_DIRS = ['.next/server', '.next/static'];
const TEXT_EXT = /\.(js|mjs|cjs|json|html|txt|map|rsc)$/;

function walk(dir) {
  let out = [];
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return out;
  }
  for (const name of entries) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) out = out.concat(walk(full));
    else if (TEXT_EXT.test(name)) out.push(full);
  }
  return out;
}

const expectIndex = process.argv.indexOf('--expect');
const expect = expectIndex === -1 ? 'absent' : process.argv[expectIndex + 1];
if (!['absent', 'present'].includes(expect)) {
  console.error('usage: verify-readonly.mjs --expect <absent|present>');
  process.exit(2);
}

// A FAILED build leaves partial output behind, and partial output is the most
// dangerous input this script can be handed: the first read-only build here
// failed on a Turbopack alias error, emitted 99 files, and this check reported
// "no trigger surface" over them. It was right — the surface was not there,
// because nothing was. A clean grep over the wreckage of a failed build is the
// exact false green the script exists to prevent, so the manifests Next writes
// only on a successful build are required first.
const BUILD_COMPLETE_MARKERS = [
  '.next/BUILD_ID',
  '.next/routes-manifest.json',
  '.next/prerender-manifest.json',
];
const missingMarkers = BUILD_COMPLETE_MARKERS.filter((m) => {
  try {
    statSync(m);
    return false;
  } catch {
    return true;
  }
});
if (missingMarkers.length > 0) {
  console.error(
    `FAIL: the build did not complete — missing ${missingMarkers.join(', ')}. ` +
      `Whatever is under .next is the residue of a failed build, and grepping it ` +
      `would report a clean result for the wrong reason.`,
  );
  process.exit(1);
}

const files = BUILD_DIRS.flatMap(walk);
if (files.length === 0) {
  console.error(
    `FAIL: no build output found under ${BUILD_DIRS.join(', ')}. ` +
      `An empty search is not a clean one.`,
  );
  process.exit(1);
}

const needles = [MARKER, ...CORROBORATING];
const hits = [];
for (const file of files) {
  let text;
  try {
    text = readFileSync(file, 'utf8');
  } catch {
    continue;
  }
  for (const needle of needles) {
    if (text.includes(needle)) hits.push({ file, needle });
  }
}

const found = [...new Set(hits.map((h) => h.needle))];

if (expect === 'absent') {
  if (hits.length === 0) {
    console.log(
      `PASS: no trigger surface in ${files.length} emitted file(s). ` +
        `Absent from the build, not disabled in it.`,
    );
    process.exit(0);
  }
  console.error(`FAIL: the read-only build contains a trigger surface.`);
  for (const h of hits.slice(0, 20)) console.error(`  ${h.needle}  ->  ${h.file}`);
  if (hits.length > 20) console.error(`  ... and ${hits.length - 20} more`);
  process.exit(1);
}

// expect === 'present'
const missing = needles.filter((n) => !found.includes(n));
if (missing.length === 0) {
  console.log(
    `PASS: the internal build contains the trigger surface ` +
      `(${hits.length} match(es) across ${files.length} file(s)). ` +
      `The absent-check is therefore capable of failing.`,
  );
  process.exit(0);
}
console.error(
  `FAIL: the internal build is MISSING ${missing.map((m) => JSON.stringify(m)).join(', ')}. ` +
    `Either the panel stopped being built, or these needles are stale — ` +
    `in which case the read-only check has been passing vacuously.`,
);
process.exit(1);
