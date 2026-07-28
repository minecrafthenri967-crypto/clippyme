import { expect, test } from 'vitest';
import { hasBurnableLayer, planAutoCompose } from './autoCompose.js';

const CLIP = { viral_hook_text: 'Wait for it', reframe_mode: 'auto' };

// The Create recipe as optsToPreselections would produce it.
const RECIPE = { subtitles: { preset: 'hormozi_bold', mode: 'karaoke' }, hook: { position: 'top' } };

function clips(n = 2) {
  return Array.from({ length: n }, (_, i) => ({ ...CLIP, video_url: `/videos/j/c${i}.mp4` }));
}

// --- hasBurnableLayer -------------------------------------------------------

test('no toggles at all → nothing to burn', () => {
  expect(hasBurnableLayer({ toggles: {} })).toBe(false);
  expect(hasBurnableLayer({})).toBe(false);
  expect(hasBurnableLayer(undefined)).toBe(false);
});

test('subtitles alone is burnable', () => {
  expect(hasBurnableLayer({ toggles: { subtitles: true } })).toBe(true);
});

test('hook toggle with empty text is NOT burnable', () => {
  // compose_layers skips the hook layer and logs a warning — composing would
  // spend a full re-encode producing a byte-identical clip.
  expect(hasBurnableLayer({ toggles: { hook: true }, hookParams: { text: '' } })).toBe(false);
  expect(hasBurnableLayer({ toggles: { hook: true }, hookParams: { text: '   ' } })).toBe(false);
  expect(hasBurnableLayer({ toggles: { hook: true }, hookParams: {} })).toBe(false);
});

test('hook toggle with text is burnable', () => {
  expect(hasBurnableLayer({ toggles: { hook: true }, hookParams: { text: 'Wait' } })).toBe(true);
});

test('grade toggle with preset "none" is NOT burnable', () => {
  expect(hasBurnableLayer({ toggles: { grade: true }, gradeParams: { preset: 'none' } })).toBe(false);
  expect(hasBurnableLayer({ toggles: { grade: true }, gradeParams: { preset: 'warm' } })).toBe(true);
});

// --- planAutoCompose --------------------------------------------------------

test('recipe with captions → every clip planned', () => {
  const plan = planAutoCompose({ clips: clips(3), preselections: RECIPE });
  expect(plan.map((p) => p.idx)).toEqual([0, 1, 2]);
  expect(plan[0].params.toggles.subtitles).toBe(true);
});

test('empty recipe → empty plan, no wasted encodes', () => {
  expect(planAutoCompose({ clips: clips(3), preselections: undefined })).toEqual([]);
  expect(planAutoCompose({ clips: clips(3), preselections: {} })).toEqual([]);
});

test('no clips → empty plan', () => {
  expect(planAutoCompose({ clips: [], preselections: RECIPE })).toEqual([]);
  expect(planAutoCompose({})).toEqual([]);
});

test('already-composed clip is skipped', () => {
  const plan = planAutoCompose({
    clips: clips(2),
    clipStates: { 0: { previewUrl: '/videos/j/composed_clip_0.mp4' } },
    preselections: RECIPE,
  });
  expect(plan.map((p) => p.idx)).toEqual([1]);
});

test('deleted and in-flight clips are skipped', () => {
  const plan = planAutoCompose({
    clips: clips(3),
    clipStates: { 0: { deleted: true }, 1: { processing: true } },
    preselections: RECIPE,
  });
  expect(plan.map((p) => p.idx)).toEqual([2]);
});

test('already-attempted indices are skipped so the effect cannot loop', () => {
  const plan = planAutoCompose({
    clips: clips(3), preselections: RECIPE, done: new Set([0, 2]),
  });
  expect(plan.map((p) => p.idx)).toEqual([1]);
});

test('composes only — baseMode matches reframeMode so no reframe is re-run', () => {
  const [entry] = planAutoCompose({ clips: clips(1), preselections: RECIPE });
  expect(entry.params.baseMode).toBe(entry.params.reframeMode);
});

test("each clip keeps its own hook text rather than the first clip's", () => {
  const two = [
    { ...CLIP, viral_hook_text: 'Erster Hook' },
    { ...CLIP, viral_hook_text: 'Zweiter Hook' },
  ];
  const plan = planAutoCompose({
    clips: two, preselections: { ...RECIPE, hook: { position: 'top' }, subtitles: RECIPE.subtitles },
  });
  expect(plan[0].params.hookParams.text).toBe('Erster Hook');
  expect(plan[1].params.hookParams.text).toBe('Zweiter Hook');
});

test('a clip the user already edited keeps its own settings, not the recipe', () => {
  const plan = planAutoCompose({
    clips: clips(1),
    clipStates: { 0: { toggles: { subtitles: true, hook: false }, subtitleParams: { preset: 'neon_glow' } } },
    preselections: RECIPE,
  });
  expect(plan[0].params.subtitleParams.preset).toBe('neon_glow');
  expect(plan[0].params.toggles.hook).toBe(false);
});

test('manual trims are never auto-applied', () => {
  // drop_ranges are per-clip content the user staged by hand; an automatic
  // pass must not silently cut footage.
  const plan = planAutoCompose({
    clips: clips(1),
    clipStates: { 0: { toggles: { subtitles: true }, dropRanges: [[1, 2]] } },
    preselections: RECIPE,
  });
  expect(plan[0].params.dropRanges).toEqual([]);
});
