import { describe, it, expect } from 'vitest';
import {
  OUTPUT_AR,
  clamp01,
  defaultGameplayBox,
  expandBoxToAspect,
  zoneAspects,
  zoneImageStyle,
} from './layoutGeometry';

// 16:9 screenshot, the common case.
const SRC_W = 1920;
const SRC_H = 1080;

describe('expandBoxToAspect', () => {
  it('only ever grows the drawn box', () => {
    const box = { x: 0.33, y: 0.43, w: 0.36, h: 0.5 };
    const out = expandBoxToAspect(box, SRC_W, SRC_H, 0.5);
    // eps: an unchanged edge round-trips through pixels and back, so exact
    // comparison trips on float noise rather than on real shrinkage.
    const eps = 1e-9;
    expect(out.x).toBeLessThanOrEqual(box.x + eps);
    expect(out.y).toBeLessThanOrEqual(box.y + eps);
    expect(out.x + out.w).toBeGreaterThanOrEqual(box.x + box.w - eps);
    expect(out.y + out.h).toBeGreaterThanOrEqual(box.y + box.h - eps);
  });

  it('produces the requested aspect ratio', () => {
    const out = expandBoxToAspect({ x: 0.3, y: 0.3, w: 0.2, h: 0.2 }, SRC_W, SRC_H, 0.5);
    expect((out.w * SRC_W) / (out.h * SRC_H)).toBeCloseTo(0.5, 4);
  });

  it('stays inside the frame when the box sits on an edge', () => {
    const out = expandBoxToAspect({ x: 0.9, y: 0.9, w: 0.1, h: 0.1 }, SRC_W, SRC_H, 0.5);
    expect(out.x).toBeGreaterThanOrEqual(0);
    expect(out.y).toBeGreaterThanOrEqual(0);
    expect(out.x + out.w).toBeLessThanOrEqual(1 + 1e-9);
    expect(out.y + out.h).toBeLessThanOrEqual(1 + 1e-9);
  });

  it('is a no-op without a box or image dimensions', () => {
    expect(expandBoxToAspect(null, SRC_W, SRC_H, 0.5)).toBeNull();
    const box = { x: 0, y: 0, w: 1, h: 1 };
    expect(expandBoxToAspect(box, 0, 0, 0.5)).toBe(box);
  });
});

describe('defaultGameplayBox', () => {
  it('is horizontally centred over the full height, like the renderer', () => {
    const out = defaultGameplayBox(SRC_W, SRC_H, 0.5);
    expect(out.y).toBe(0);
    expect(out.h).toBe(1);
    // Centred: equal margin either side.
    expect(out.x).toBeCloseTo(1 - out.x - out.w, 6);
  });

  it('never exceeds the source width for a wide zone', () => {
    const out = defaultGameplayBox(SRC_W, SRC_H, 4);
    expect(out.w).toBeLessThanOrEqual(1);
  });
});

describe('zoneImageStyle', () => {
  it('scales and offsets so the box fills the zone', () => {
    const s = zoneImageStyle({ x: 0.25, y: 0.5, w: 0.5, h: 0.25 });
    expect(s.width).toBe('200%');
    expect(s.height).toBe('400%');
    expect(s.left).toBe('-50%');
    expect(s.top).toBe('-200%');
  });

  it('hides itself rather than dividing by zero', () => {
    expect(zoneImageStyle(null).display).toBe('none');
    expect(zoneImageStyle({ x: 0, y: 0, w: 0, h: 0 }).display).toBe('none');
  });
});

describe('zoneAspects', () => {
  it('splits the output height between the two zones', () => {
    const { top, bottom, split } = zoneAspects(0.4);
    expect(split).toBe(0.4);
    expect(top).toBeCloseTo(OUTPUT_AR / 0.4, 6);
    expect(bottom).toBeCloseTo(OUTPUT_AR / 0.6, 6);
  });

  it('clamps to the same range the renderer enforces', () => {
    expect(zoneAspects(0.99).split).toBe(0.75);
    expect(zoneAspects(0.01).split).toBe(0.15);
    expect(zoneAspects(undefined).split).toBe(0.45);
  });
});

describe('clamp01', () => {
  it('bounds and tolerates NaN from a mid-drag pointer read', () => {
    expect(clamp01(1.5)).toBe(1);
    expect(clamp01(-2)).toBe(0);
    expect(clamp01(NaN)).toBe(0);
    expect(clamp01(0.42)).toBe(0.42);
  });
});
