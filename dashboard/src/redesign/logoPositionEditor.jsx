// ClippyMe redesign — LogoPositionEditor: drag the logo anywhere on a 9:16
// preview and resize it, instead of picking from S/M/L + 7 corner presets.
//
// `value` is `{ x, y, scale }`:
//   x, y  — 0..1 fraction of the space the logo has left to move in, i.e. the
//           same `(main_w-overlay_w)*x` normalized placement ffmpeg uses
//           (`logo_overlay_xy` in domain/logo.py) — 0 flush against the start
//           edge, 1 flush against the end edge, at any logo size.
//   scale — logo width as a fraction of the frame width (0.05..0.5).
// Named corner presets keep working server-side for old recipes; this editor
// only ever writes the free-placement shape once the user drags.
import { useCallback, useEffect, useRef, useState } from 'react';
import { useT } from '../i18n/context.jsx';
import { clamp01 } from '../lib/layoutGeometry';

const BOX_HEIGHT = 340;
const BOX_WIDTH = BOX_HEIGHT * (9 / 16);
const MIN_SCALE = 0.05;
const MAX_SCALE = 0.5;

export function LogoPositionEditor({ value, onChange, imgUrl, logoUrl }) {
  const t = useT();
  const containerRef = useRef(null);
  const drag = useRef(null); // 'move' | 'resize' | null
  const [dims, setDims] = useState({ w: 0, h: 0 });

  const [logoError, setLogoError] = useState(false);
  const v = { x: 0.85, y: 0.05, scale: 0.18, ...value };
  const logoAR = dims.w && dims.h ? dims.w / dims.h : 1;
  const logoWpx = Math.round(BOX_WIDTH * v.scale);
  const logoHpx = Math.round(logoWpx / logoAR);
  const leftPx = v.x * (BOX_WIDTH - logoWpx);
  const topPx = v.y * (BOX_HEIGHT - logoHpx);

  const onLogoLoad = (e) => {
    setDims({ w: e.target.naturalWidth || 1, h: e.target.naturalHeight || 1 });
  };

  const grabOffset = useRef({ dx: 0, dy: 0 });
  const resizeStart = useRef({ scale: 0.18, px: 0 });

  const onMovePointerDown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const rect = e.currentTarget.getBoundingClientRect();
    grabOffset.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
    drag.current = 'move';
    containerRef.current?.setPointerCapture?.(e.pointerId);
  };

  const onResizePointerDown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    resizeStart.current = { scale: v.scale, px: e.clientX };
    drag.current = 'resize';
    containerRef.current?.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = useCallback((e) => {
    if (!drag.current || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    if (drag.current === 'move') {
      const wpx = Math.round(BOX_WIDTH * v.scale);
      const hpx = Math.round(wpx / logoAR);
      const left = e.clientX - rect.left - grabOffset.current.dx;
      const top = e.clientY - rect.top - grabOffset.current.dy;
      const spanX = BOX_WIDTH - wpx;
      const spanY = BOX_HEIGHT - hpx;
      const nx = spanX > 0 ? clamp01(left / spanX) : 0.5;
      const ny = spanY > 0 ? clamp01(top / spanY) : 0.5;
      onChange({ x: nx, y: ny });
    } else if (drag.current === 'resize') {
      const deltaPx = e.clientX - resizeStart.current.px;
      const nextScale = resizeStart.current.scale + deltaPx / BOX_WIDTH;
      onChange({ scale: Math.min(MAX_SCALE, Math.max(MIN_SCALE, nextScale)) });
    }
  }, [v.scale, logoAR, onChange]);

  useEffect(() => {
    const end = () => { drag.current = null; };
    window.addEventListener('pointerup', end);
    window.addEventListener('pointermove', onPointerMove);
    return () => {
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointermove', onPointerMove);
    };
  }, [onPointerMove]);

  return (
    <div>
      <div
        ref={containerRef}
        style={{
          position: 'relative', width: BOX_WIDTH, height: BOX_HEIGHT,
          background: '#000', borderRadius: 8, overflow: 'hidden', touchAction: 'none',
        }}
      >
        {imgUrl ? (
          <img src={imgUrl} alt={t('logoEditor.previewAlt')} draggable={false}
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }} />
        ) : (
          <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center',
            color: '#666', fontSize: 12, textAlign: 'center', padding: 12 }}>
            {t('logoEditor.noPreview')}
          </div>
        )}
        <div
          onPointerDown={onMovePointerDown}
          style={{
            position: 'absolute', left: leftPx, top: topPx, width: logoWpx, height: logoHpx,
            cursor: 'grab', border: '1px dashed rgba(255,255,255,.6)', boxSizing: 'border-box',
          }}
        >
          {logoUrl && !logoError ? (
            <img src={logoUrl} alt={t('logoEditor.logoAlt')} onLoad={onLogoLoad}
              onError={() => setLogoError(true)} draggable={false}
              style={{ width: '100%', height: '100%', display: 'block', userSelect: 'none' }} />
          ) : (
            <div style={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center',
              background: 'rgba(247,188,89,.25)', color: '#f7bc59', fontSize: 11 }}>
              {t('logoEditor.logoAlt')}
            </div>
          )}
          <div
            onPointerDown={onResizePointerDown}
            style={{
              position: 'absolute', right: -6, bottom: -6, width: 14, height: 14,
              borderRadius: '50%', background: 'var(--brand-amber)', cursor: 'nwse-resize',
              border: '2px solid #000',
            }}
          />
        </div>
      </div>
      <div className="od" style={{ marginTop: 8, maxWidth: BOX_WIDTH }}>{t('logoEditor.hint')}</div>
    </div>
  );
}
