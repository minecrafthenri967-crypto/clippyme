import { test } from 'vitest';
import assert from 'node:assert/strict';

// Vitest runs with a jsdom environment, so window.location.origin is real —
// the old plain-Node `globalThis.window` stub is gone.
import { optsToPreselections, clipVideoSrc, clipPreviewSrc, fmtDuration, exportClip } from './realApi.js';

// --- optsToPreselections: the Create-tab → backend translation layer --------

test('reframe mode: legacy object alias normalizes to subject', () => {
  assert.equal(optsToPreselections({ reframeMode: 'object' }).reframe_mode, 'subject');
  assert.equal(optsToPreselections({ reframeMode: 'subject' }).reframe_mode, 'subject');
  assert.equal(optsToPreselections({ reframeMode: 'disabled' }).reframe_mode, 'disabled');
});

test('reframe mode: legacy boolean fallback, default auto', () => {
  assert.equal(optsToPreselections({}).reframe_mode, 'auto');
  assert.equal(optsToPreselections({ reframe: false }).reframe_mode, 'disabled');
});

test('model override: blank is omitted, value passes through trimmed', () => {
  assert.equal(optsToPreselections({}).model, undefined);
  assert.equal(optsToPreselections({ model: '  ' }).model, undefined);
  assert.equal(optsToPreselections({ model: ' gemini-2.5-pro ' }).model, 'gemini-2.5-pro');
});

test('karaoke subtitles carry colours but not classic typography', () => {
  const p = optsToPreselections({
    subtitles: true, subMode: 'karaoke', subPreset: 'hormozi_bold',
    subColor: '#FDE700', subStroke: '#111111', subFontSize: 48,
  });
  assert.equal(p.subtitles.preset, 'hormozi_bold');
  assert.equal(p.subtitles.font_color, '#FDE700');
  assert.equal(p.subtitles.outline_color, '#111111');
  assert.equal(p.subtitles.font_size, 48);
  assert.equal('font' in p.subtitles, false, 'classic-only font key leaked into karaoke');
});

test('karaoke font_size 0 means Auto and is omitted', () => {
  const p = optsToPreselections({ subtitles: true, subMode: 'karaoke', subFontSize: 0 });
  assert.equal('font_size' in p.subtitles, false);
});

test('zernio_profile defaults to "default" and passes a chosen campaign through', () => {
  assert.equal(optsToPreselections({}).zernio_profile, 'default');
  assert.equal(optsToPreselections({ zernioProfile: 'dja' }).zernio_profile, 'dja');
});

test('gaming facecam box defaults to null and passes a drawn box through', () => {
  assert.equal(optsToPreselections({}).gaming_facecam_box, null);
  const box = { x: 0.6, y: 0.05, w: 0.35, h: 0.3 };
  assert.deepEqual(optsToPreselections({ gamingFacecamBox: box }).gaming_facecam_box, box);
});

test('classic subtitles carry font/border/background', () => {
  const p = optsToPreselections({
    subtitles: true, subMode: 'classic', subFont: 'Anton-Regular',
    subColor: '#581BBA', subOutlineW: 3, subBg: true,
  });
  assert.equal(p.subtitles.font, 'Anton-Regular');
  assert.equal(p.subtitles.font_color, '#581BBA');
  assert.equal(p.subtitles.border_width, 3);
  assert.equal(p.subtitles.bg_opacity, 0.6);
  assert.equal(p.subtitles.bg_color, '#000000');
});

test('subtitles off → false; grade none → false; logo off → false', () => {
  const p = optsToPreselections({ subtitles: false, gradePreset: 'none', logo: false });
  assert.equal(p.subtitles, false);
  assert.equal(p.grade, false);
  assert.equal(p.logo, false);
});

test('grade preset flows through when set', () => {
  assert.deepEqual(optsToPreselections({ gradePreset: 'vivid_pop' }).grade, { preset: 'vivid_pop' });
});

// --- URL safety: a malicious API response must never become an executable src

test('clipVideoSrc neutralizes javascript: and data: schemes', () => {
  for (const evil of ['javascript:alert(1)', 'data:text/html,<script>x</script>']) {
    const src = clipVideoSrc({ video_url: evil });
    assert.equal(src.startsWith('javascript:'), false);
    assert.equal(src.startsWith('data:'), false);
    assert.equal(src.startsWith('/'), true, `expected inert relative path, got ${src}`);
  }
});

test('clipVideoSrc appends the cache-buster correctly', () => {
  assert.equal(clipVideoSrc({ video_url: '/videos/j/clip_1.mp4' }, 99).endsWith('?v=99'), true);
  assert.equal(clipVideoSrc({ video_url: '/videos/j/c.mp4?x=1' }, 99).endsWith('&v=99'), true);
});

test('clipPreviewSrc prefers the composed previewUrl over the raw clip', () => {
  const clip = { video_url: '/videos/j/clip_1.mp4' };
  const raw = clipPreviewSrc(clip, {});
  assert.equal(raw.includes('clip_1.mp4'), true);
  const composed = clipPreviewSrc(clip, { previewUrl: '/videos/j/composed_clip_0.mp4', previewBust: 7 });
  assert.equal(composed.includes('composed_clip_0.mp4'), true);
  assert.equal(composed.endsWith('?v=7'), true);
});

test('fmtDuration renders m:ss with zero-padded seconds', () => {
  assert.equal(fmtDuration(0, 65), '1:05');
  assert.equal(fmtDuration(10, 10), '0:00');
  assert.equal(fmtDuration(0, 599.6), '10:00');
});

// --- I-1 regression: per-clip endpoints must resolve by the backend's
// ABSOLUTE `shorts` position (`original_index`), not the frontend array
// position — they diverge once a manual-publish gap skips a
// deleted_after_publish clip (job_results._build_clips).

test('exportClip composes against clip.original_index, not the array position', async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (url) => {
    calls.push(url);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ composed_url: '/videos/j/composed.mp4' }) });
  };
  try {
    // Clip B sits at array position 1 but is absolute shorts position 2 (a
    // deleted_after_publish clip at position 1 was skipped upstream).
    const clip = { original_index: 2, video_url: '/videos/j/clip_3.mp4' };
    const state = { toggles: { subtitles: true } };
    await exportClip('job-1', 1, clip, state, {});
    assert.equal(calls.length, 1);
    assert.match(String(calls[0]), /\/api\/compose\/job-1\/2$/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

// --- stream-layout editor guides reach the backend --------------------------
// The editor's hook/subtitle guides are dragged on the 9:16 PREVIEW, so they
// are heights on the delivered frame — hooks.parse_hook_position_fraction /
// subtitles.parse_position_fraction take exactly that in place of a keyword.
// They used to be stored in opts and mapped nowhere, so dragging them did
// nothing at all.

test('gaming layout: dragged hook guide becomes the hook position', () => {
  const p = optsToPreselections({
    reframeMode: 'gaming', hooks: true, hookPos: 'top', gamingHookY: 0.42,
  });
  assert.equal(p.hook.position, 0.42);
});

test('gaming layout: dragged subtitle guide becomes the subtitle position', () => {
  const p = optsToPreselections({
    reframeMode: 'gaming', subtitles: true, subPosition: 'bottom', gamingSubtitleY: 0.8,
  });
  assert.equal(p.subtitles.position, 0.8);
});

test('gaming layout: undragged guides leave the keyword pickers in charge', () => {
  const p = optsToPreselections({
    reframeMode: 'gaming', hooks: true, hookPos: 'seam', subtitles: true, subPosition: 'bottom',
  });
  assert.equal(p.hook.position, 'seam');
  assert.equal(p.subtitles.position, 'bottom');
});

test('guides only apply in gaming mode (the only surface the editor has)', () => {
  const p = optsToPreselections({
    reframeMode: 'auto', hooks: true, hookPos: 'top', gamingHookY: 0.42,
    subtitles: true, subPosition: 'bottom', gamingSubtitleY: 0.8,
  });
  assert.equal(p.hook.position, 'top');
  assert.equal(p.subtitles.position, 'bottom');
});

test('hook style (background) survives the translation', () => {
  const p = optsToPreselections({
    hooks: true, hookPos: 'top',
    hookStyle: { bg_enabled: true, bg_color: '#FF0000', bg_opacity: 0.9 },
  });
  assert.equal(p.hook.bg_enabled, true);
  assert.equal(p.hook.bg_color, '#FF0000');
});

// --- compose-time layer toggles reaching the backend -----------------------
//
// optsToPreselections is the ONLY bridge from the Create tab's flat `opts` to
// the preselections that seedToggles turns into backend toggles. A layer
// missing here is a silent dead end: the switch flips in the UI and nothing
// downstream ever hears about it.

test('player image toggle reaches preselections (was missing entirely — dead-end switch)', () => {
  const pre = optsToPreselections({
    playerImage: true, playerImagePos: 'top-left', playerImageSize: 'L',
  });
  assert.deepEqual(pre.player_image, { position: 'top-left', size: 'L' });
});

test('player image off yields a falsy preselection so seedToggles leaves it off', () => {
  assert.equal(optsToPreselections({ playerImage: false }).player_image, false);
});

test('cold-open teaser toggle reaches preselections', () => {
  assert.equal(optsToPreselections({ teaser: true }).teaser, true);
  assert.equal(optsToPreselections({}).teaser, false);
});

test('subtitle pop toggle reaches preselections only in karaoke mode', () => {
  const pre = optsToPreselections({ subtitles: true, subMode: 'karaoke', subPop: true });
  assert.equal(pre.subtitles.pop, true);
});

test('subtitle pop toggle does not leak into classic mode preselections', () => {
  const pre = optsToPreselections({ subtitles: true, subMode: 'classic', subPop: true });
  assert.equal('pop' in pre.subtitles, false);
});
