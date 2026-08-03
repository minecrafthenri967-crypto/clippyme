// presets.js — pins the PRESET_KEYS capture list (a missing key here silently
// drops that setting from every save/default-preset round trip) and the
// one-click saveAsDefault flow.
import { test, expect, beforeEach } from 'vitest';
import {
  PRESET_KEYS, captureOpts, saveAsDefault, loadUserPresets,
  getDefaultPresetId, getDefaultPresetOpts,
} from './presets';

beforeEach(() => localStorage.clear());

test('PRESET_KEYS includes every subtitle fine-tuning field the drawer sets', () => {
  for (const k of ['subFontSize', 'subStroke', 'subOutlineW', 'subBg', 'subAlign', 'subOffsetY']) {
    expect(PRESET_KEYS).toContain(k);
  }
});

test('PRESET_KEYS includes the hook fields the drawer sets', () => {
  for (const k of ['hookPos', 'hookSize', 'hookStyle']) {
    expect(PRESET_KEYS).toContain(k);
  }
});

test('PRESET_KEYS includes the gaming facecam fields the Gaming hint sets', () => {
  for (const k of ['gamingFacecamPosition', 'gamingFacecamSize']) {
    expect(PRESET_KEYS).toContain(k);
  }
});

test('captureOpts only keeps keys that are present and in PRESET_KEYS', () => {
  const captured = captureOpts({
    subFontSize: 42, subStroke: '#00ff00', url: 'https://youtu.be/x', mode: 'single',
  });
  expect(captured).toEqual({ subFontSize: 42, subStroke: '#00ff00' });
});

test('saveAsDefault stores a preset, marks it default, and seeds getDefaultPresetOpts', () => {
  const opts = { subFontSize: 42, subStroke: '#00ff00', hookPos: 'top', clips: 5 };
  const preset = saveAsDefault(opts);

  expect(preset.title).toBe('My default');
  expect(getDefaultPresetId()).toBe(preset.id);
  expect(getDefaultPresetOpts()).toEqual(captureOpts(opts));
});

test('saveAsDefault called twice overwrites in place instead of piling up presets', () => {
  saveAsDefault({ clips: 3 });
  saveAsDefault({ clips: 9 });

  const mine = loadUserPresets().filter((p) => p.title === 'My default');
  expect(mine).toHaveLength(1);
  expect(getDefaultPresetOpts()).toEqual({ clips: 9 });
});
