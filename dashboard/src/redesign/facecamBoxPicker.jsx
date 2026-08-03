// ClippyMe redesign — FacecamBoxPicker: lets a user pin the Gaming reframe
// mode's facecam region precisely by drawing a rectangle over their OWN
// screenshot of the stream. The screenshot itself never leaves the browser —
// it is only a drawing aid (URL.createObjectURL), never uploaded — only the
// resulting {x,y,w,h} fractions (0..1 of the screenshot's rendered size) are
// sent to the backend (see resolve_facecam_box_from_fractions), so it does
// not matter that the screenshot's own pixel size differs from the actual
// source video's, only that they share the same aspect ratio.
import { useRef, useState } from 'react';
import { Icon, Btn } from './primitives';
import { useT } from '../i18n/context.jsx';

const MIN_DRAG_FRACTION = 0.02;

export function FacecamBoxPicker({ box, onChange }) {
  const t = useT();
  const [imgUrl, setImgUrl] = useState(null);
  const containerRef = useRef(null);
  const dragStart = useRef(null);
  const [draft, setDraft] = useState(null);

  const onFile = (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setImgUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
  };

  const fracFromEvent = (e) => {
    const rect = containerRef.current.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
    };
  };

  const onPointerDown = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const start = fracFromEvent(e);
    dragStart.current = start;
    setDraft({ x: start.x, y: start.y, w: 0, h: 0 });
    // Not implemented in jsdom (test environment) — guard rather than crash.
    containerRef.current.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e) => {
    if (!dragStart.current) return;
    const { x, y } = fracFromEvent(e);
    const { x: x0, y: y0 } = dragStart.current;
    setDraft({ x: Math.min(x0, x), y: Math.min(y0, y), w: Math.abs(x - x0), h: Math.abs(y - y0) });
  };

  const onPointerUp = () => {
    if (draft && draft.w >= MIN_DRAG_FRACTION && draft.h >= MIN_DRAG_FRACTION) onChange(draft);
    dragStart.current = null;
    setDraft(null);
  };

  const rect = dragStart.current ? draft : box;

  return (
    <div>
      <label className="btn btn-secondary btn-sm" style={{ cursor: 'pointer', display: 'inline-flex' }}>
        <Icon n="image" />{t('create.reframe.gamingUploadScreenshot')}
        <input type="file" accept="image/*" hidden onChange={onFile} />
      </label>
      {imgUrl && (
        <>
          <div
            ref={containerRef}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            style={{ position: 'relative', marginTop: 10, touchAction: 'none', cursor: 'crosshair', lineHeight: 0 }}
          >
            <img src={imgUrl} alt={t('create.reframe.gamingScreenshotAlt')} draggable={false}
              style={{ width: '100%', display: 'block', borderRadius: 8, userSelect: 'none' }} />
            {rect && rect.w > 0 && rect.h > 0 && (
              <div style={{
                position: 'absolute', left: `${rect.x * 100}%`, top: `${rect.y * 100}%`,
                width: `${rect.w * 100}%`, height: `${rect.h * 100}%`,
                border: '2px solid var(--brand-amber)', background: 'rgba(247,188,89,.15)',
                pointerEvents: 'none', boxSizing: 'border-box',
              }} />
            )}
          </div>
          <div className="od" style={{ marginTop: 8 }}>{t('create.reframe.gamingDrawHint')}</div>
        </>
      )}
      {box && (
        <Btn variant="secondary" size="sm" icon="x" style={{ marginTop: 10 }} onClick={() => onChange(null)}>
          {t('create.reframe.gamingResetToAuto')}
        </Btn>
      )}
    </div>
  );
}
