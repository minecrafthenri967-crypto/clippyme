// ClippyMe redesign — LogoControls + GradeControls: the logo/grade config
// pieces shared by the Create recipe and the EditClipModal (hookStyle.jsx
// precedent: controlled values + partial-emitting onChange, UI only).
import { Segmented } from './primitives';
import { PLAYER_IMAGE_POSITIONS, PLAYER_IMAGE_SIZES, GRADE_PRESETS } from './data';
import { LogoPositionEditor } from './logoPositionEditor';

// A legacy corner preset ("top-right" etc.) has no x/y fraction — approximate
// where it would sit so dragging starts from roughly the same spot instead of
// jumping to a default corner the first time an old recipe is opened here.
const _LEGACY_XY = {
  'top-left': { x: 0, y: 0 }, 'top-center': { x: 0.5, y: 0 }, 'top-right': { x: 1, y: 0 },
  'bottom-left': { x: 0, y: 1 }, 'bottom-center': { x: 0.5, y: 1 }, 'bottom-right': { x: 1, y: 1 },
  center: { x: 0.5, y: 0.5 },
};
const _LEGACY_SCALE = { S: 0.12, M: 0.18, L: 0.26 };

/** Normalize a logo position (legacy keyword OR {x,y} drag value) + size
 * (legacy S/M/L OR a free scale) into the shape LogoPositionEditor wants. */
export function logoEditorValue(position, size, scale) {
  const xy = position && typeof position === 'object' ? position : (_LEGACY_XY[position] || _LEGACY_XY['top-right']);
  return { x: xy.x, y: xy.y, scale: scale || _LEGACY_SCALE[size] || 0.18 };
}

// Free drag-and-resize positioning over a still preview — replaces the old
// 7-corner-preset + S/M/L picker so the user isn't limited to fixed spots.
export function LogoControls({ position, size, scale, onChange, imgUrl, logoUrl }) {
  const value = logoEditorValue(position, size, scale);
  return (
    <LogoPositionEditor
      value={value}
      imgUrl={imgUrl}
      logoUrl={logoUrl}
      onChange={(partial) => {
        const next = { ...value, ...partial };
        onChange({ position: { x: next.x, y: next.y }, scale: next.scale });
      }}
    />
  );
}

// Same shape as LogoControls — the athlete-photo "flash" overlay (eBay Live
// campaign etc.) shares the same position-preset/size-preset UI pattern.
export function PlayerImageControls({ position, size, onChange }) {
  return (
    <>
      <div className="cf-row">
        <span className="field-label" style={{ marginBottom: 9, display: 'flex' }}>Position</span>
        <div className="seg-grid">
          {PLAYER_IMAGE_POSITIONS.map(([v, l]) => (
            <button key={v} type="button" className={'seg-cell' + (position === v ? ' on' : '')}
              onClick={() => onChange({ position: v })}>{l}</button>
          ))}
        </div>
      </div>
      <div className="cf-row">
        <span className="field-label" style={{ marginBottom: 9, display: 'flex' }}>Size</span>
        <Segmented full value={size} onChange={(id) => onChange({ size: id })}
          options={PLAYER_IMAGE_SIZES.map(([v, l]) => ({ id: v, label: l }))} />
      </div>
    </>
  );
}

// Just the preset segmented — the surrounding chrome genuinely differs per
// surface (Create: always-visible row with an explicit 'Off' entry, no
// Switch; modal: an on/off Switch owns the none↔preset transition).
export function GradeControls({ preset, onChange, withOff = false, full = true }) {
  const options = withOff
    ? [{ id: 'none', label: 'Off' }, ...GRADE_PRESETS.map((g) => ({ id: g.id, label: g.label }))]
    : GRADE_PRESETS;
  return (
    <Segmented full={full} value={preset} onChange={(id) => onChange({ preset: id })}
      options={options} />
  );
}
