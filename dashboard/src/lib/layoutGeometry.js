// Pure geometry for the stream-layout editor's live 9:16 preview.
//
// These mirror `reframe_ops.expand_box_to_aspect` / `create_gaming_frame` so
// the preview shows what the renderer will actually produce. Keeping them here
// (rather than inline in the component) is what makes that equivalence
// testable — a preview that quietly disagrees with the backend is worse than
// no preview, because the whole point of drawing a layout is to see it first.

/** Aspect ratio (w/h) of the delivered vertical frame. */
export const OUTPUT_AR = 9 / 16;

/**
 * Grow `box` (0..1 fractions) to `targetAR`, centred, clamped to the frame.
 *
 * Only ever GROWS, matching the Python: whatever the user drew stays fully
 * visible. `srcW`/`srcH` are the screenshot's natural pixel size — the box is
 * fractional but aspect ratios are not, so the conversion needs them.
 */
export function expandBoxToAspect(box, srcW, srcH, targetAR) {
  if (!box || !srcW || !srcH || !targetAR) return box;
  const cx = (box.x + box.w / 2) * srcW;
  const cy = (box.y + box.h / 2) * srcH;
  const w = box.w * srcW;
  const h = box.h * srcH;
  const currentAR = h ? w / h : targetAR;
  let nw;
  let nh;
  if (currentAR < targetAR) {
    nw = h * targetAR;
    nh = h;
  } else {
    nw = w;
    nh = w / targetAR;
  }
  nw = Math.min(nw, srcW);
  nh = Math.min(nh, srcH);
  const nx = Math.max(0, Math.min(srcW - nw, cx - nw / 2));
  const ny = Math.max(0, Math.min(srcH - nh, cy - nh / 2));
  return { x: nx / srcW, y: ny / srcH, w: nw / srcW, h: nh / srcH };
}

/**
 * The centred full-height crop the renderer falls back to when no gameplay
 * box was drawn, expressed in the same fractional shape so the preview can
 * show the default rather than going blank.
 */
export function defaultGameplayBox(srcW, srcH, zoneAR) {
  if (!srcW || !srcH || !zoneAR) return null;
  const cropW = Math.min(srcW, srcH * zoneAR);
  return { x: (srcW - cropW) / 2 / srcW, y: 0, w: cropW / srcW, h: 1 };
}

/**
 * CSS for the <img> inside a preview zone so that `box` exactly fills it.
 *
 * Assumes `box` was already aspect-expanded to the zone, which is what keeps
 * width and height scaling consistent — without that the image would stretch,
 * showing a distortion the renderer never produces.
 */
export function zoneImageStyle(box) {
  if (!box || !box.w || !box.h) return { display: 'none' };
  return {
    position: 'absolute',
    width: `${100 / box.w}%`,
    height: `${100 / box.h}%`,
    left: `${(-box.x / box.w) * 100}%`,
    top: `${(-box.y / box.h) * 100}%`,
    maxWidth: 'none',
  };
}

/** Clamp a 0..1 fraction, tolerating NaN from a mid-drag pointer read. */
export function clamp01(value) {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/**
 * Zone aspect ratios for a given split, as the renderer computes them:
 * the top zone gets `split` of the height, the bottom gets the rest.
 */
export function zoneAspects(split) {
  const s = Math.min(0.75, Math.max(0.15, Number(split) || 0.45));
  return { top: OUTPUT_AR / s, bottom: OUTPUT_AR / (1 - s), split: s };
}
