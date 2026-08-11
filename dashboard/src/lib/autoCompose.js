// Decides which freshly-finished clips should have their layers burned in
// right away, instead of waiting for the user to hit Download.
//
// The pipeline renders clips RAW: subtitles, hook, grade, logo and banner are
// compose-time layers (see domain/compose.py). That made the Create recipe look
// broken — you tick "captions", watch the preview, and see no captions, because
// nothing had composed yet. This module drives the compose right after a job
// completes, so the preview shows what the clip will actually look like.
//
// It stays *pure* (a plan in, no I/O) so the "should this clip compose?" rules
// are unit-testable without mounting the app or faking fetch. RedesignApp feeds
// the plan to the same bounded-concurrency runner the bulk-apply paths use.
//
// Editing afterwards is unaffected: composing writes a SEPARATE composed_clip_N
// file and never touches the raw clip or the preserved 16:9 source slice, so
// reframe switching and re-composing with different settings keep working.
import { buildClipParams, clipStateToParams } from './bulkApply.js';

/**
 * Is there at least one layer that would actually change a pixel?
 *
 * Composing costs a full re-encode per clip, so a plan entry that the backend
 * would skip anyway is worse than useless. Two toggles can be "on" and still
 * burn nothing:
 *   - `hook` with empty text — compose_layers logs a warning and skips it.
 *   - `grade` with preset "none" — already filtered by seedToggles, but a
 *     hand-edited clip state can still carry it.
 */
export function hasBurnableLayer(params) {
  const toggles = params?.toggles;
  if (!toggles) return false;
  const hookUsable = !!(toggles.hook && String(params?.hookParams?.text || '').trim());
  const gradeUsable = !!(toggles.grade && (params?.gradeParams?.preset || 'none') !== 'none');
  return !!(
    toggles.subtitles || toggles.smartcut || toggles.logo ||
    toggles.banner || toggles.player_image || toggles.teaser || hookUsable || gradeUsable
  );
}

/**
 * Build the {idx, clip, params} plan for auto-composing a finished job's clips.
 *
 * @param {object[]} clips           results.clips
 * @param {object} clipStates        per-clip saved state (may be empty)
 * @param {object} preselections     the Create recipe
 * @param {Set<number>} done         indices already attempted this session
 * @returns {Array<{idx:number, clip:object, params:object}>}
 */
export function planAutoCompose({ clips = [], clipStates = {}, preselections, done } = {}) {
  const seen = done || new Set();
  const plan = [];
  for (let i = 0; i < clips.length; i += 1) {
    if (seen.has(i)) continue;
    const state = clipStates[i];
    // Never fight the user: a removed clip, one already composed (previewUrl),
    // or one mid-render is left exactly as it is.
    if (state?.deleted || state?.previewUrl || state?.processing) continue;

    const clip = clips[i];
    // Reuse the bulk-apply seeding so auto-compose and "Apply to all" can never
    // disagree about what the recipe means. Passing the clip's own state as the
    // target keeps its per-clip hook text.
    const params = buildClipParams(
      clipStateToParams(state, preselections, clip), clip, state,
    );
    if (!hasBurnableLayer(params)) continue;
    // baseMode === reframeMode, so runApplyEdit composes without re-running the
    // (expensive) reframe subprocess the pipeline already did.
    plan.push({ idx: i, clip, params: { ...params, baseMode: params.reframeMode } });
  }
  return plan;
}
