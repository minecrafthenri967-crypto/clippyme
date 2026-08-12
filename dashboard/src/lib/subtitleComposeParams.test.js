import { test } from 'vitest';
import assert from 'node:assert/strict';

import { toComposeSubtitleParams, fromComposeSubtitleParams } from './subtitleComposeParams.js';

// --- pop (word bounce): forwarded only in karaoke mode, never leaked into classic ---

test('toComposeSubtitleParams forwards pop=true in karaoke mode', () => {
  const out = toComposeSubtitleParams({ mode: 'karaoke', pop: true });
  assert.equal(out.pop, true);
});

test('toComposeSubtitleParams omits pop entirely when off (matches font_size convention)', () => {
  const out = toComposeSubtitleParams({ mode: 'karaoke', pop: false });
  assert.equal('pop' in out, false);
});

test('toComposeSubtitleParams never emits pop in classic mode', () => {
  const out = toComposeSubtitleParams({ mode: 'classic', pop: true });
  assert.equal('pop' in out, false);
});

test('fromComposeSubtitleParams seeds pop from a persisted recipe', () => {
  const out = fromComposeSubtitleParams({ pop: true }, { pop: false });
  assert.equal(out.pop, true);
});

test('fromComposeSubtitleParams falls back to defaults when pop is absent', () => {
  const out = fromComposeSubtitleParams({ mode: 'karaoke' }, { pop: false });
  assert.equal(out.pop, false);
});
