// ClippyMe redesign — Create flow: presets + source + calm options recipe.
import { useState, useRef, useEffect } from 'react';
import { Icon, Btn, Panel, Segmented, Switch, Stepper } from './primitives';
import { Hero } from './chrome';
import { LANGUAGES, GEMINI_MODELS, HOOK_STYLE_DEFAULT } from './data';
import { HookStyleControls, HookPreview } from './hookStyle';
import { SubtitleControls } from './subtitleControls';
import { LogoControls, PlayerImageControls, GradeControls } from './layerControls';
import { BannerControls } from './bannerControls';
import { getZernioProfiles } from './realApi';
import { validateCreateOptions } from '../lib/createValidation';
import { useT } from '../i18n/context.jsx';

function PresetCards({ presets, active, defaultId, onPick, onSetDefault, onDelete, onSaveCurrent }) {
  const t = useT();
  const corner = { position: 'absolute', top: 12, left: 12, display: 'flex', gap: 8, zIndex: 2 };
  return (
    <div className="preset-row">
      {presets.map((p) => (
        // role=button (not a real <button>) so the star/trash actions inside
        // can be real, keyboard-operable <button>s — interactive-in-interactive
        // is invalid HTML.
        <div key={p.id} role="button" tabIndex={0} aria-pressed={active === p.id} className={'preset' + (active === p.id ? ' on' : '')}
          onClick={() => onPick(p)}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(p); } }}>
          <span className="pcheck"><Icon n="check" /></span>
          <span style={corner}>
            <button type="button" className="picon-btn"
              title={defaultId === p.id ? t('create.preset.unsetTitle') : t('create.preset.setDefaultTitle')}
              aria-label={defaultId === p.id ? t('create.preset.unsetAria') : t('create.preset.setDefaultAria')}
              onClick={(e) => { e.stopPropagation(); onSetDefault(p.id); }}
              style={{ color: defaultId === p.id ? 'var(--brand-amber)' : 'var(--fg-4)' }}>
              <Icon n="star" style={{ width: 14, height: 14 }} />
            </button>
            {p.user && (
              <button type="button" className="picon-btn" title={t('create.preset.delete')} aria-label={t('create.preset.delete')}
                onClick={(e) => { e.stopPropagation(); onDelete(p.id); }} style={{ color: 'var(--fg-4)' }}>
                <Icon n="trash-2" style={{ width: 14, height: 14 }} />
              </button>
            )}
          </span>
          <span className="pico"><Icon n={p.icon} /></span>
          <span className="pt">{p.title}{defaultId === p.id && <span style={{ color: 'var(--brand-amber)', fontSize: 11, marginLeft: 6 }}>{t('create.preset.defaultBadge')}</span>}</span>
          <span className="pd">{p.desc}</span>
        </div>
      ))}
      <button type="button" className="preset" onClick={onSaveCurrent}
        style={{ borderStyle: 'dashed', alignItems: 'center', justifyContent: 'center', textAlign: 'center' }}>
        <span className="pico"><Icon n="plus" /></span>
        <span className="pt">{t('create.preset.saveCurrent')}</span>
        <span className="pd">{t('create.preset.saveCurrentDesc')}</span>
      </button>
    </div>
  );
}

function SourcePanel({ opts, set }) {
  const t = useT();
  const [drag, setDrag] = useState(false);
  const fileInput = useRef(null);
  const batchInput = useRef(null);
  const batchLines = opts.batch.split('\n').filter((l) => l.trim());
  const batchFileCount = (opts.batchFiles || []).length;
  const totalQueued = batchLines.length + batchFileCount;
  const pickFile = (f) => f && set({ file: f, fileName: f.name });

  // Named Zernio profiles (separate campaigns, each with its own account).
  // "default" always exists implicitly — the picker itself only renders once
  // a second profile is created, so a single-campaign install stays unchanged
  // (same convention as live.jsx / publish.jsx).
  const [zernioProfiles, setZernioProfiles] = useState([{ id: 'default', label: 'Default', configured: false }]);
  useEffect(() => { getZernioProfiles().then((r) => setZernioProfiles(r.profiles || [])).catch(() => {}); }, []);

  return (
    <Panel title={t('create.source.title')} sub={t('create.source.sub')} icon="link"
      headRight={
        <Segmented value={opts.mode} onChange={(id) => set({ mode: id })}
          options={[{ id: 'single', label: t('create.source.mode.single'), icon: 'square' }, { id: 'batch', label: t('create.source.mode.batch'), icon: 'layers' }]} />
      }>
      {zernioProfiles.length > 1 && (
        <div className="field" style={{ marginBottom: 14 }}>
          <span className="field-label">{t('create.campaign.label')}</span>
          <Segmented value={opts.zernioProfile || 'default'} onChange={(id) => set({ zernioProfile: id })}
            options={zernioProfiles.map((p) => ({ id: p.id, label: p.label }))} />
        </div>
      )}
      {opts.mode === 'single' ? (
        <div>
          <Segmented full value={opts.source} onChange={(id) => set({ source: id })}
            options={[{ id: 'url', label: t('create.source.type.url'), icon: 'globe' }, { id: 'file', label: t('create.source.type.upload'), icon: 'file-up' }]} />
          <div style={{ height: 14 }} />
          {opts.source === 'url' ? (
            <div className="input">
              <Icon n="link" />
              <input value={opts.url} placeholder={t('create.source.urlPlaceholder')}
                onChange={(e) => set({ url: e.target.value })} />
              <button type="button" className="paste" onClick={async () => {
                try {
                  const text = await navigator.clipboard.readText();
                  if (text) set({ url: text.trim() });
                } catch {
                  /* clipboard blocked (no permission / insecure context) — no-op */
                }
              }}>
                <Icon n="clipboard" />{t('create.source.pasteBtn')}
              </button>
            </div>
          ) : (
            <div className={'dropzone' + (opts.file ? ' has' : drag ? ' drag' : '')}
              role="button" tabIndex={0}
              aria-label={opts.file ? t('create.source.removeVideoAria') : t('create.source.chooseVideoAria')}
              onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => { e.preventDefault(); setDrag(false); pickFile(e.dataTransfer.files?.[0]); }}
              onClick={() => { if (opts.file) { set({ file: null, fileName: '' }); } else { fileInput.current?.click(); } }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.currentTarget.click(); }
              }}>
              <input ref={fileInput} type="file" accept="video/*,.mp4,.mov,.webm,.mkv,.m4v,.avi" hidden
                onChange={(e) => pickFile(e.target.files?.[0])} />
              <div className="dz-ico"><Icon n={opts.file ? 'file-video' : 'upload'} /></div>
              {opts.file ? (
                <div><b>{opts.fileName}</b><div className="label" style={{ marginTop: 6 }}>{t('create.source.readyLabel')}</div></div>
              ) : (
                <div>{t('create.source.dropPrefix')} <b style={{ color: 'var(--brand-blue)' }}>{t('create.source.browse')}</b>
                  <div className="label" style={{ marginTop: 6, textTransform: 'none', letterSpacing: 0 }}>{t('create.source.fileHint')}</div></div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div>
          <div className="field">
            <span className="field-label"><Icon n="globe" /> {t('create.batch.urlsLabel')}</span>
            <textarea className="ta mono" rows="4" value={opts.batch}
              placeholder={'https://youtube.com/watch?v=a1\nhttps://youtube.com/watch?v=b2'}
              onChange={(e) => set({ batch: e.target.value })}></textarea>
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <div className="dropzone" style={{ padding: 18 }}
              role="button" tabIndex={0} aria-label={t('create.batch.addFilesAria')}
              onClick={() => batchInput.current?.click()}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); batchInput.current?.click(); }
              }}>
              <input ref={batchInput} type="file" accept="video/*,.mp4,.mov,.webm,.mkv,.m4v,.avi" hidden multiple
                onChange={(e) => set({ batchFiles: [...(opts.batchFiles || []), ...Array.from(e.target.files || [])] })} />
              <Icon n="plus" style={{ width: 16, height: 16 }} /> {t('create.batch.addFiles')}
            </div>
          </div>
          {batchFileCount > 0 && (
            <div className="s-sub" style={{ marginTop: 10 }}>
              {(opts.batchFiles || []).map((f, i) => (
                <span key={i} className="chip">{f.name.slice(0, 22)}</span>
              ))}
              <button type="button" className="chip" style={{ cursor: 'pointer', color: 'var(--danger)', background: 'none', border: 'none', font: 'inherit' }} onClick={() => set({ batchFiles: [] })}>{t('create.batch.clearFiles')}</button>
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--line-1)' }}>
            <span className="label">{t('create.batch.queuedLabel')}</span>
            <span className="label tnum" style={{ color: totalQueued > 20 ? 'var(--danger)' : totalQueued ? 'var(--brand-amber)' : 'var(--fg-4)' }}>
              {String(totalQueued).padStart(2, '0')} / 20
            </span>
          </div>
        </div>
      )}

      <div style={{ marginTop: 18, paddingTop: 18, borderTop: '1px solid var(--line-1)' }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <span className="field-label"><Icon n="sparkles" style={{ color: 'var(--brand-blue)' }} /> {t('create.ai.instructionsLabel')}</span>
          <textarea className="ta" rows="2" value={opts.instructions}
            placeholder={t('create.ai.instructionsPlaceholder')}
            onChange={(e) => set({ instructions: e.target.value })}></textarea>
        </div>
      </div>
    </Panel>
  );
}

function OptRow({ icon, label, desc, on, set, onConfig, configActive }) {
  const t = useT();
  return (
    <div className={'opt' + (on ? ' on' : '')}>
      <div className="oico"><Icon n={icon} /></div>
      <div className="otxt">
        <div className="ot">{label}</div>
        <div className="od">{desc}</div>
      </div>
      <div className="r">
        {onConfig && on && (
          <button type="button" className={'cfg' + (configActive ? ' active' : '')} onClick={onConfig} aria-label={t('create.configureAria', { label })}>
            <Icon n="sliders-horizontal" />
          </button>
        )}
        <Switch on={on} onChange={set} />
      </div>
    </div>
  );
}

// Adapter over the shared SubtitleControls: resolves the recipe's inline
// defaults into a fully-populated value object (the shared component never
// applies defaults) and reverse-maps the seedClipParams-vocabulary partials
// back onto the persisted sub* opts keys.
const SUB_KEYMAP = {
  mode: 'subMode', preset: 'subPreset', font: 'subFont', font_color: 'subColor',
  outline_color: 'subStroke', font_size: 'subFontSize', border_width: 'subOutlineW',
  bg: 'subBg', position: 'subPosition', align: 'subAlign', offset_y: 'subOffsetY',
};

function SubConfig({ opts, set }) {
  const value = {
    mode: opts.subMode,
    preset: opts.subPreset,
    font: opts.subFont || 'Montserrat-Black',
    font_color: opts.subColor || '#FFFFFF',
    outline_color: opts.subStroke || '#000000',
    font_size: opts.subFontSize || 0,
    border_width: opts.subOutlineW ?? 2,
    bg: !!opts.subBg,
    position: opts.subPosition || 'bottom',
    align: opts.subAlign || 'center',
    offset_y: opts.subOffsetY || 0,
  };
  const onChange = (partial) => {
    const patch = {};
    for (const [k, v] of Object.entries(partial)) patch[SUB_KEYMAP[k]] = v;
    set(patch);
  };
  return <SubtitleControls variant="create" value={value} onChange={onChange} />;
}

function HookConfig({ opts, set }) {
  const t = useT();
  const hs = opts.hookStyle || HOOK_STYLE_DEFAULT;
  const setStyle = (partial) => set({ hookStyle: { ...HOOK_STYLE_DEFAULT, ...hs, ...partial } });
  return (
    <div className="cfg-drawer fade-in">
      <HookPreview text={t('create.hook.previewText')} style={hs} />
      <div className="cf-row" style={{ marginTop: 12 }}>
        <span className="field-label" style={{ marginBottom: 9, display: 'flex' }}>{t('create.hook.positionLabel')}</span>
        <Segmented full value={opts.hookPos} onChange={(id) => set({ hookPos: id })}
          options={[{ id: 'top', label: t('create.hook.pos.top') }, { id: 'center', label: t('create.hook.pos.center') }, { id: 'bottom', label: t('create.hook.pos.bottom') }]} />
      </div>
      <div className="cf-row">
        <span className="field-label" style={{ marginBottom: 9, display: 'flex' }}>{t('create.hook.sizeLabel')}</span>
        <Segmented full value={opts.hookSize} onChange={(id) => set({ hookSize: id })}
          options={[{ id: 'S', label: t('create.hook.size.small') }, { id: 'M', label: t('create.hook.size.medium') }, { id: 'L', label: t('create.hook.size.large') }]} />
      </div>
      <HookStyleControls style={hs} set={setStyle} />
    </div>
  );
}

function LogoConfig({ opts, set }) {
  const t = useT();
  return (
    <div className="cfg-drawer fade-in">
      <LogoControls position={opts.logoPos || 'top-right'} size={opts.logoSize || 'M'}
        onChange={(p) => set(p.position !== undefined
          ? { logoPos: p.position } : { logoSize: p.size })} />
      <div className="od" style={{ marginTop: 2 }}>{t('create.logo.uploadHint')}</div>
    </div>
  );
}

function PlayerImageConfig({ opts, set }) {
  const t = useT();
  return (
    <div className="cfg-drawer fade-in">
      <PlayerImageControls position={opts.playerImagePos || 'center'} size={opts.playerImageSize || 'M'}
        onChange={(p) => set(p.position !== undefined
          ? { playerImagePos: p.position } : { playerImageSize: p.size })} />
      <div className="od" style={{ marginTop: 2 }}>{t('create.playerImage.uploadHint')}</div>
    </div>
  );
}

function BannerConfig({ opts, set }) {
  const value = {
    platform: opts.bannerPlatform || 'kick',
    handle: opts.bannerHandle || '',
    y_pct: opts.bannerYPct ?? 0.85,
  };
  const onChange = (partial) => {
    const patch = {};
    if (partial.platform !== undefined) patch.bannerPlatform = partial.platform;
    if (partial.handle !== undefined) patch.bannerHandle = partial.handle;
    if (partial.y_pct !== undefined) patch.bannerYPct = partial.y_pct;
    set(patch);
  };
  return (
    <div className="cfg-drawer fade-in">
      <BannerControls value={value} onChange={onChange} />
    </div>
  );
}

function OptionsPanel({ opts, set, onSaveAsDefault }) {
  const t = useT();
  const [subCfg, setSubCfg] = useState(false);
  const [hookCfg, setHookCfg] = useState(false);
  const [logoCfg, setLogoCfg] = useState(false);
  const [bannerCfg, setBannerCfg] = useState(false);
  const [playerImageCfg, setPlayerImageCfg] = useState(false);
  return (
    <Panel title={t('create.recipe.title')} sub={t('create.recipe.sub')} icon="sliders-horizontal"
      headRight={
        <Btn variant="secondary" size="sm" icon="save" onClick={onSaveAsDefault}
          title={t('create.saveDefault.hint')}>
          {t('create.saveDefault.button')}
        </Btn>
      }>
      <div className="label" style={{ marginBottom: 4 }}>{t('create.recipe.outputLabel')}</div>
      <div className="opt">
        <div className="oico"><Icon n="scissors" /></div>
        <div className="otxt">
          <div className="ot">{t('create.clips.label')}</div>
          <div className="od">{opts.clipsAuto ? t('create.clips.descAuto') : t('create.clips.descCustom')}</div>
        </div>
        <div className="r" style={{ gap: 9 }}>
          {!opts.clipsAuto && <Stepper value={opts.clips} set={(v) => set({ clips: v })} />}
          <Segmented value={opts.clipsAuto ? 'auto' : 'custom'}
            onChange={(id) => set({ clipsAuto: id === 'auto' })}
            options={[{ id: 'auto', label: t('create.clips.auto') }, { id: 'custom', label: t('create.clips.set') }]} />
        </div>
      </div>
      <div className="opt">
        <div className="oico"><Icon n="crop" /></div>
        <div className="otxt"><div className="ot">{t('create.aspect.label')}</div><div className="od">{t('create.aspect.desc')}</div></div>
        <div className="r"><Segmented value={opts.aspect} onChange={(id) => set({ aspect: id })}
          options={[{ id: '9:16', label: '9:16' }, { id: '1:1', label: '1:1' }, { id: '16:9', label: '16:9' }]} /></div>
      </div>

      <div className="label" style={{ margin: '16px 0 4px' }}>{t('create.section.aiReframe')}</div>
      <OptRow icon="sparkles" label={t('create.detect.label')} desc={t('create.detect.desc')}
        on={opts.detect} set={(v) => set({ detect: v })} />
      {opts.detect && (
        <div className="opt">
          <div className="oico"><Icon n="sparkles" /></div>
          <div className="otxt" style={{ flex: 1 }}><div className="ot">{t('create.model.label')}</div><div className="od">{t('create.model.desc')}</div></div>
          <div className="r" style={{ flex: '0 0 184px' }}>
            <select className="sel" value={opts.model || ''} onChange={(e) => set({ model: e.target.value })}>
              {GEMINI_MODELS.map(([v, l]) => <option key={v || 'default'} value={v}>{l}</option>)}
            </select>
          </div>
        </div>
      )}
      <div className="opt">
        <div className="oico"><Icon n="scan-face" /></div>
        <div className="otxt"><div className="ot">{t('create.reframe.label')}</div><div className="od">{t('create.reframe.desc')}</div></div>
        <div className="r"><Segmented value={(opts.reframeMode === 'object' ? 'subject' : opts.reframeMode) || (opts.reframe === false ? 'disabled' : 'auto')} onChange={(id) => set({ reframeMode: id })}
          options={[{ id: 'auto', label: t('create.reframe.auto') }, { id: 'subject', label: t('create.reframe.subject') }, { id: 'gaming', label: t('create.reframe.gaming') }, { id: 'disabled', label: t('create.reframe.off') }]} /></div>
      </div>
      {opts.reframeMode === 'gaming' && (
        <div className="od" style={{ marginTop: -8, marginBottom: 14 }}>{t('create.reframe.gamingHint')}</div>
      )}
      <OptRow icon="scissors" label={t('create.smartcut.label')} desc={t('create.smartcut.desc')}
        on={opts.smartcut} set={(v) => set({ smartcut: v })} />
      <OptRow icon="zoom-in" label={t('create.zoom.label')} desc={t('create.zoom.desc')}
        on={opts.zoom} set={(v) => set({ zoom: v })} />
      <div className="opt">
        <div className="oico"><Icon n="languages" /></div>
        <div className="otxt" style={{ flex: 1 }}><div className="ot">{t('create.language.label')}</div><div className="od">{t('create.language.desc')}</div></div>
        <div className="r" style={{ flex: '0 0 184px' }}>
          <select className="sel" value={opts.language} onChange={(e) => set({ language: e.target.value })}>
            {LANGUAGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
      </div>

      <div className="label" style={{ margin: '16px 0 4px' }}>{t('create.section.captionsHooks')}</div>
      <OptRow icon="captions" label={t('create.subtitles.label')} desc={t('create.subtitles.desc')}
        on={opts.subtitles} set={(v) => set({ subtitles: v })} onConfig={() => setSubCfg(!subCfg)} configActive={subCfg} />
      {opts.subtitles && subCfg && <SubConfig opts={opts} set={set} />}
      <OptRow icon="type" label={t('create.hooks.label')} desc={t('create.hooks.desc')}
        on={opts.hooks} set={(v) => set({ hooks: v })} onConfig={() => setHookCfg(!hookCfg)} configActive={hookCfg} />
      {opts.hooks && hookCfg && <HookConfig opts={opts} set={set} />}
      <OptRow icon="stamp" label={t('create.logo.label')} desc={t('create.logo.desc')}
        on={opts.logo} set={(v) => set({ logo: v })} onConfig={() => setLogoCfg(!logoCfg)} configActive={logoCfg} />
      {opts.logo && logoCfg && <LogoConfig opts={opts} set={set} />}
      <OptRow icon="rss" label={t('create.banner.label')} desc={t('create.banner.desc')}
        on={opts.banner} set={(v) => set({ banner: v })} onConfig={() => setBannerCfg(!bannerCfg)} configActive={bannerCfg} />
      {opts.banner && bannerCfg && <BannerConfig opts={opts} set={set} />}
      <OptRow icon="image" label={t('create.playerImage.label')} desc={t('create.playerImage.desc')}
        on={opts.playerImage} set={(v) => set({ playerImage: v })}
        onConfig={() => setPlayerImageCfg(!playerImageCfg)} configActive={playerImageCfg} />
      {opts.playerImage && playerImageCfg && <PlayerImageConfig opts={opts} set={set} />}
      <div className="opt">
        <div className="oico"><Icon n="palette" /></div>
        <div className="otxt"><div className="ot">{t('create.grade.label')}</div><div className="od">{t('create.grade.desc')}</div></div>
        <div className="r"><GradeControls withOff full={false} preset={opts.gradePreset || 'none'}
          onChange={(p) => set({ gradePreset: p.preset })} /></div>
      </div>
    </Panel>
  );
}

function SummaryBar({ opts, ready, count, onCreate, error }) {
  const t = useT();
  const chips = [
    opts.aspect || '9:16',
    opts.clipsAuto ? t('create.summary.chip.autoClips') : t('create.summary.chip.clipsCount', { count: opts.clips }),
    opts.detect ? t('create.summary.chip.viralDetect') : t('create.summary.chip.wholeVideo'),
    (() => { const m = opts.reframeMode || (opts.reframe === false ? 'disabled' : 'auto'); return (m === 'subject' || m === 'object') ? t('create.summary.chip.subjectCrop') : m === 'gaming' ? t('create.summary.chip.gaming') : m === 'disabled' ? t('create.summary.chip.letterbox') : t('create.summary.chip.reframe'); })(),
    opts.smartcut && t('create.summary.chip.smartCut'),
    opts.subtitles && t('create.summary.chip.subsSuffix', { mode: opts.subMode }),
    opts.hooks && t('create.summary.chip.hooks'),
  ].filter(Boolean);
  return (
    <div className="summary">
      <div>
        <div className="s-main">
          {ready
            ? (opts.clipsAuto
              ? t('create.summary.autoMain')
              : `${t('create.summary.aimingFor')} ${count} ${count === 1 ? t('create.summary.clipSingular') : t('create.summary.clipPlural')}`)
            : t('create.summary.noSource')}
        </div>
        <div className="s-sub">
          {chips.map((c) => <span key={c} className="chip">{c}</span>)}
        </div>
        {error && <div className="field-error" role="alert">{error}</div>}
      </div>
      <div className="s-right">
        <Btn variant="grad" size="lg" icon="wand-sparkles" onClick={onCreate} disabled={!ready}>{t('create.summary.createBtn')}</Btn>
      </div>
    </div>
  );
}

export function CreateView({ opts, set, onPickPreset, onCreate, presets, defaultId, onSetDefault, onDelete, onSaveCurrent, onSaveAsDefault }) {
  const t = useT();
  const validation = validateCreateOptions(opts);
  const ready = validation.valid;
  const nSources = Math.max(1, validation.sourceCount);
  const count = opts.detect ? opts.clips * nSources : nSources;
  return (
    <div className="container fade-in">
      <Hero eyebrow={t('create.hero.eyebrow')} line1={t('create.hero.line1')} grad={t('create.hero.grad')}
        sub={t('create.hero.sub')} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        {/* Order: pick a source first, then optionally start from a preset,
            then fine-tune the recipe by hand. */}
        <SourcePanel opts={opts} set={set} />
        <div>
          <div className="label" style={{ marginBottom: 12 }}>{t('create.presets.introLabel')}</div>
          <PresetCards presets={presets} active={opts.preset} defaultId={defaultId}
            onPick={onPickPreset} onSetDefault={onSetDefault} onDelete={onDelete} onSaveCurrent={onSaveCurrent} />
        </div>
        <OptionsPanel opts={opts} set={set} onSaveAsDefault={onSaveAsDefault} />
      </div>
      <SummaryBar opts={opts} ready={ready} count={count} onCreate={onCreate} error={validation.firstError} />
    </div>
  );
}
