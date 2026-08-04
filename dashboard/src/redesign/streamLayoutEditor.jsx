// ClippyMe redesign — StreamLayoutEditor: build a per-streamer layout by
// drawing it, then save it as a reusable template.
//
// Two panels, matching how the two halves of the problem actually differ:
//   LEFT  — the stream screenshot. Draw which SOURCE regions are the facecam
//           and the gameplay. These are crops taken from the incoming video.
//   RIGHT — a live 9:16 preview assembled from exactly those crops. Drag the
//           facecam/gameplay split and the hook + subtitle heights. These are
//           positions on the DELIVERED frame and have no meaning on the source
//           screenshot, which is why they are not drawn on the left.
//
// The screenshot never leaves the browser (URL.createObjectURL); only 0..1
// fractions are saved, so a template drawn over a 1080p screenshot applies
// unchanged to a 4K source of the same aspect ratio.
import { useCallback, useEffect, useRef, useState } from 'react';
import { Icon, Btn } from './primitives';
import { useT } from '../i18n/context.jsx';
import {
  clamp01,
  defaultGameplayBox,
  expandBoxToAspect,
  zoneAspects,
  zoneImageStyle,
} from '../lib/layoutGeometry';

const MIN_DRAG_FRACTION = 0.02;
const PREVIEW_HEIGHT = 340;

const REGION_STYLE = {
  facecam: { border: '2px solid var(--brand-amber)', background: 'rgba(247,188,89,.15)' },
  gameplay: { border: '2px solid #e5484d', background: 'rgba(229,72,77,.12)' },
};

/** Draw-on-screenshot panel: picks whichever region is armed. */
function SourcePanel({ imgUrl, onImgLoad, region, setRegion, value, onChange, t }) {
  const containerRef = useRef(null);
  const dragStart = useRef(null);
  const [draft, setDraft] = useState(null);

  const fracFromEvent = (e) => {
    const rect = containerRef.current.getBoundingClientRect();
    return {
      x: clamp01((e.clientX - rect.left) / rect.width),
      y: clamp01((e.clientY - rect.top) / rect.height),
    };
  };

  const onPointerDown = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const start = fracFromEvent(e);
    dragStart.current = start;
    setDraft({ x: start.x, y: start.y, w: 0, h: 0 });
    containerRef.current.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e) => {
    if (!dragStart.current) return;
    const { x, y } = fracFromEvent(e);
    const { x: x0, y: y0 } = dragStart.current;
    setDraft({
      x: Math.min(x0, x), y: Math.min(y0, y),
      w: Math.abs(x - x0), h: Math.abs(y - y0),
    });
  };

  const onPointerUp = () => {
    if (draft && draft.w >= MIN_DRAG_FRACTION && draft.h >= MIN_DRAG_FRACTION) {
      onChange({ [region]: draft });
    }
    dragStart.current = null;
    setDraft(null);
  };

  const live = dragStart.current ? { [region]: draft } : {};

  return (
    <div style={{ flex: '1 1 320px', minWidth: 260 }}>
      <div className="field-label" style={{ marginBottom: 8 }}>
        {t('layout.source.title')}
      </div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}>
        {['facecam', 'gameplay'].map((id) => (
          <button
            key={id}
            type="button"
            className={`btn btn-sm ${region === id ? 'btn-primary' : 'btn-secondary'}`}
            aria-pressed={region === id}
            onClick={() => setRegion(id)}
          >
            {t(`layout.region.${id}`)}
          </button>
        ))}
      </div>
      {imgUrl ? (
        <div
          ref={containerRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          style={{ position: 'relative', touchAction: 'none', cursor: 'crosshair', lineHeight: 0 }}
        >
          <img src={imgUrl} alt={t('layout.source.alt')} draggable={false} onLoad={onImgLoad}
            style={{ width: '100%', display: 'block', borderRadius: 8, userSelect: 'none' }} />
          {['facecam', 'gameplay'].map((id) => {
            const r = live[id] || value[id];
            if (!r || !r.w || !r.h) return null;
            return (
              <div key={id} style={{
                position: 'absolute', left: `${r.x * 100}%`, top: `${r.y * 100}%`,
                width: `${r.w * 100}%`, height: `${r.h * 100}%`,
                pointerEvents: 'none', boxSizing: 'border-box', ...REGION_STYLE[id],
              }}>
                <span style={{
                  position: 'absolute', top: 0, left: 0, fontSize: 11, lineHeight: '14px',
                  padding: '0 4px', background: 'rgba(0,0,0,.6)', color: '#fff',
                }}>{t(`layout.region.${id}`)}</span>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="od">{t('layout.source.needScreenshot')}</div>
      )}
      <div className="od" style={{ marginTop: 8 }}>{t('layout.source.hint')}</div>
    </div>
  );
}

/** A draggable horizontal guide on the 9:16 preview. */
function Guide({ id, y, color, label, onDrag, dashed }) {
  const onPointerDown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    onDrag.start(id);
  };
  return (
    <div
      role="slider"
      aria-label={label}
      aria-valuenow={Math.round(y * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={(e) => {
        if (e.key === 'ArrowUp') onDrag.nudge(id, -0.01);
        if (e.key === 'ArrowDown') onDrag.nudge(id, 0.01);
      }}
      style={{
        position: 'absolute', left: 0, right: 0, top: `${y * 100}%`,
        height: 0, borderTop: `2px ${dashed ? 'dashed' : 'solid'} ${color}`,
        cursor: 'ns-resize', touchAction: 'none', zIndex: 2,
      }}
    >
      <span style={{
        position: 'absolute', right: 2, top: -16, fontSize: 10, lineHeight: '14px',
        padding: '0 4px', background: color, color: '#000', borderRadius: 3,
        whiteSpace: 'nowrap', pointerEvents: 'none',
      }}>{label}</span>
    </div>
  );
}

/** Live 9:16 preview assembled from the drawn source crops. */
function PreviewPanel({ imgUrl, srcW, srcH, value, onChange, t }) {
  const boxRef = useRef(null);
  const dragging = useRef(null);
  const { top: topAR, bottom: bottomAR, split } = zoneAspects(value.split);

  const facecamCrop = value.facecam && srcW
    ? expandBoxToAspect(value.facecam, srcW, srcH, topAR) : null;
  const rawGameplay = value.gameplay || (srcW ? defaultGameplayBox(srcW, srcH, bottomAR) : null);
  const gameplayCrop = rawGameplay && srcW
    ? expandBoxToAspect(rawGameplay, srcW, srcH, bottomAR) : null;

  const yFromEvent = useCallback((e) => {
    const rect = boxRef.current.getBoundingClientRect();
    return clamp01((e.clientY - rect.top) / rect.height);
  }, []);

  const apply = useCallback((id, y) => {
    if (id === 'split') {
      onChange({ split: Math.min(0.75, Math.max(0.15, y)) });
    } else {
      onChange({ [id]: y });
    }
  }, [onChange]);

  const onPointerMove = (e) => {
    if (!dragging.current) return;
    apply(dragging.current, yFromEvent(e));
  };

  const drag = {
    start: (id) => { dragging.current = id; },
    nudge: (id, delta) => {
      const current = id === 'split' ? split : (value[id] ?? 0.5);
      apply(id, clamp01(current + delta));
    },
  };

  // A pointerup outside the preview must still end the drag, or the guide
  // keeps following the cursor after the button is released.
  useEffect(() => {
    const end = () => { dragging.current = null; };
    window.addEventListener('pointerup', end);
    return () => window.removeEventListener('pointerup', end);
  }, []);

  const hookY = value.hook_y ?? split;
  const subtitleY = value.subtitle_y ?? 0.78;

  return (
    <div style={{ flex: '0 0 auto' }}>
      <div className="field-label" style={{ marginBottom: 8 }}>{t('layout.preview.title')}</div>
      <div
        ref={boxRef}
        onPointerMove={onPointerMove}
        style={{
          position: 'relative', height: PREVIEW_HEIGHT, width: PREVIEW_HEIGHT * (9 / 16),
          background: '#000', borderRadius: 8, overflow: 'hidden', touchAction: 'none',
        }}
      >
        <div style={{ position: 'absolute', inset: 0, top: 0, height: `${split * 100}%`, overflow: 'hidden' }}>
          {imgUrl && facecamCrop
            ? <img src={imgUrl} alt="" draggable={false} style={zoneImageStyle(facecamCrop)} />
            : <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center',
                color: '#888', fontSize: 11 }}>{t('layout.region.facecam')}</div>}
        </div>
        <div style={{ position: 'absolute', left: 0, right: 0, top: `${split * 100}%`, bottom: 0, overflow: 'hidden' }}>
          {imgUrl && gameplayCrop
            ? <img src={imgUrl} alt="" draggable={false} style={zoneImageStyle(gameplayCrop)} />
            : <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center',
                color: '#888', fontSize: 11 }}>{t('layout.region.gameplay')}</div>}
        </div>
        <Guide id="split" y={split} color="#e5484d" label={t('layout.guide.split')} onDrag={drag} />
        <Guide id="hook_y" y={hookY} color="#f7bc59" label={t('layout.guide.hook')} onDrag={drag} dashed />
        <Guide id="subtitle_y" y={subtitleY} color="#46d17a" label={t('layout.guide.subtitle')} onDrag={drag} dashed />
      </div>
      <div className="od" style={{ marginTop: 8, maxWidth: 220 }}>{t('layout.preview.hint')}</div>
    </div>
  );
}

export function StreamLayoutEditor({ value, onChange }) {
  const t = useT();
  const [imgUrl, setImgUrl] = useState(null);
  const [dims, setDims] = useState({ w: 0, h: 0 });
  const [region, setRegion] = useState('facecam');
  const v = value || {};

  const onFile = (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setImgUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
  };

  const onImgLoad = (e) => {
    setDims({ w: e.target.naturalWidth || 0, h: e.target.naturalHeight || 0 });
  };

  return (
    <div>
      <label className="btn btn-secondary btn-sm" style={{ cursor: 'pointer', display: 'inline-flex' }}>
        <Icon n="image" />{t('layout.uploadScreenshot')}
        <input type="file" accept="image/*" hidden onChange={onFile} />
      </label>
      <div style={{ display: 'flex', gap: 20, marginTop: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <SourcePanel imgUrl={imgUrl} onImgLoad={onImgLoad} region={region} setRegion={setRegion}
          value={v} onChange={onChange} t={t} />
        <PreviewPanel imgUrl={imgUrl} srcW={dims.w} srcH={dims.h} value={v} onChange={onChange} t={t} />
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
        {['facecam', 'gameplay'].map((id) => v[id] && (
          <Btn key={id} variant="secondary" size="sm" icon="x" onClick={() => onChange({ [id]: null })}>
            {t(`layout.clear.${id}`)}
          </Btn>
        ))}
      </div>
    </div>
  );
}
