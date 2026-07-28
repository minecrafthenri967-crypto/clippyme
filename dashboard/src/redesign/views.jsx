// ClippyMe redesign — HistoryView + SettingsView + ApiKeyModal, wired to the
// real backend (history list/restore/delete; config keys, cookies, Zernio).
import { useState, useEffect, useRef } from 'react';
import { useModalA11y } from './useModalA11y';
import { Icon, Btn, Badge, Switch, Segmented, Panel } from './primitives';
import { Hero } from './chrome';
import {
  getConfig, saveConfig, getModels, cookiesStatus, uploadCookies, deleteCookies,
  getZernio, saveZernio, discoverZernioAccounts,
  listFonts, uploadFont, deleteFont, logoStatus, uploadLogo, deleteLogo,
} from './realApi';
import { SUB_FONTS } from './data';
import { getApiToken, setApiToken } from '../lib/apiToken';
import { useT } from '../i18n/context.jsx';

// Curated fallback when live discovery is unavailable (no key yet / offline).
// Mirrors the allow-list prefixes (gemini-2.5- / gemini-3) the backend accepts.
const FALLBACK_MODELS = [
  { name: 'gemini-3.5-flash', display_name: 'Gemini 3.5 Flash — recommended' },
  { name: 'gemini-2.5-flash', display_name: 'Gemini 2.5 Flash — budget' },
  { name: 'gemini-3.1-pro-preview', display_name: 'Gemini 3.1 Pro — max quality' },
  { name: 'gemini-2.5-pro', display_name: 'Gemini 2.5 Pro — max quality' },
];

function relTime(ts) {
  if (!ts) return '';
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function HistoryView({ history, availableIds, onOpen, onDelete, onClear }) {
  const t = useT();
  if (!history.length) {
    return (
      <div className="container narrow fade-in">
        <Hero eyebrow={t('history.hero.eyebrow')} line1={t('history.hero.emptyTitle')} sub={t('history.hero.emptySub')} />
        <div className="empty">
          <div className="ei"><Icon n="clock" /></div>
          <h3>{t('history.empty.title')}</h3>
          <p>{t('history.empty.body')}</p>
        </div>
      </div>
    );
  }
  return (
    <div className="container narrow fade-in">
      <div className="results-head" style={{ marginBottom: 18 }}>
        <h2>{t('history.heading')}</h2>
        <Badge tone="out">{t('history.jobsCount', { count: history.length })}</Badge>
        <div className="rh-right">
          <Btn variant="ghost" size="sm" icon="trash-2" onClick={onClear}>{t('history.clearAll')}</Btn>
        </div>
      </div>
      <Panel pad={false} className="hlist">
        {history.map((h) => {
          // `availableIds` is the set of jobs whose files still exist on disk
          // (null = backend not reached yet → assume available, don't disable).
          // An entry whose files were wiped by a rebuild is shown muted + flagged
          // "files removed" instead of looking clickable and dead-ending.
          const onDisk = !availableIds || availableIds.has(h.jobId);
          const ok = h.status === 'complete' && onDisk;
          const removed = !!availableIds && !availableIds.has(h.jobId);
          return (
            <div className="hrow" key={h.jobId}
              role={ok ? 'button' : undefined} tabIndex={ok ? 0 : undefined}
              aria-label={ok ? t('history.openJobAria', { title: h.title || h.source || h.jobId }) : undefined}
              onClick={() => ok && onOpen(h)}
              onKeyDown={(e) => { if (ok && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onOpen(h); } }}
              style={{ cursor: ok ? 'pointer' : 'default', opacity: removed ? 0.55 : 1 }}>
              <div className="hthumb" style={{ background: removed ? 'var(--bg-4)' : 'var(--grad-viral)' }}>{h.clipCount ?? 0}</div>
              <div style={{ minWidth: 0 }}>
                <div className="ht" title={h.title || h.source || h.jobId}
                  style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.title || h.source || h.jobId}</div>
                <div className="hm">
                  <Icon n={h.sourceType === 'url' ? 'globe' : 'file-video'} style={{ width: 11, height: 11, verticalAlign: '-1px', marginRight: 5 }} />
                  {removed ? t('history.filesRemoved')
                    : `${h.clipCount || 0} ${t('history.clipsWord')}${h.cost != null ? ` · $${Number(h.cost).toFixed(2)}` : ''} · ${relTime(h.timestamp)}`}
                </div>
              </div>
              <div className="hr">
                {!removed && h.publishedCount > 0 && (
                  <Badge tone="teal" icon="send">{t('history.publishedBadge', { count: h.publishedCount })}</Badge>
                )}
                {removed ? <Badge tone="out" icon="triangle-alert">{t('history.badge.unavailable')}</Badge>
                  : h.status === 'complete' ? <Badge tone="teal" icon="check">{t('history.badge.complete')}</Badge>
                    : h.status === 'error' ? <Badge tone="danger" icon="triangle-alert">{t('history.badge.error')}</Badge>
                      : <Badge tone="amber" icon="clock">{h.status || t('history.badge.pending')}</Badge>}
                <button type="button" className="mini" title={t('history.deleteTitle')} aria-label={t('history.deleteAria')} onClick={(e) => { e.stopPropagation(); onDelete(h.jobId); }}><Icon n="trash-2" /></button>
                {ok && <Icon n="chevron-right" style={{ width: 18, height: 18, color: 'var(--fg-4)' }} />}
              </div>
            </div>
          );
        })}
      </Panel>
    </div>
  );
}

function KeyRow({ icon, name, desc, value, onChange, onSave, onClear, placeholder, present }) {
  const t = useT();
  const [reveal, setReveal] = useState(false);
  // Only persist (and toast) when the field actually changed during this focus
  // session — tabbing past an already-set key shouldn't spam saves/toasts.
  const focusVal = useRef(value);
  return (
    <div className="keyrow">
      <div className="ki"><Icon n={icon} /></div>
      <div style={{ minWidth: 0 }}>
        <div className="kt">{name}</div>
        <div className="kd">{desc}</div>
      </div>
      <div className="kr">
        <input className="key-input" type={reveal ? 'text' : 'password'} value={value}
          aria-label={name}
          placeholder={placeholder} onChange={(e) => onChange(e.target.value)}
          onFocus={() => { focusVal.current = value; }}
          onBlur={() => { if (value !== focusVal.current) onSave(); }} />
        <button type="button" className="mini" title={reveal ? t('settings.key.hide') : t('settings.key.show')} aria-label={reveal ? t('settings.key.hideAria') : t('settings.key.showAria')} onClick={() => setReveal(!reveal)}><Icon n={reveal ? 'eye-off' : 'eye'} /></button>
        {/* Backend-confirmed state only — raw input text (typed but not yet
            saved) must never flip this badge. */}
        {present ? <Badge tone="teal" icon="check">{t('settings.key.set')}</Badge> : <Badge tone="out">{t('settings.key.empty')}</Badge>}
        {present && onClear && (
          <button type="button" className="mini" title={t('settings.key.clearTitle')} aria-label={t('settings.key.clearAria', { name })} onClick={onClear}><Icon n="x" /></button>
        )}
      </div>
    </div>
  );
}

export function SettingsView({ apiKey, onApiKey, cookiesConfigured, onCookiesChange, pushToast }) {
  const t = useT();
  const [gemini, setGemini] = useState(apiKey || '');
  const [deepgram, setDeepgram] = useState('');
  const [elevenlabs, setElevenlabs] = useState('');
  const [hf, setHf] = useState('');
  const [twitchId, setTwitchId] = useState('');
  const [twitchSecret, setTwitchSecret] = useState('');
  const [apiToken, setApiTokenState] = useState(() => getApiToken());
  const [present, setPresent] = useState({});
  const [zernio, setZernioState] = useState(null);
  const [zKey, setZKey] = useState('');
  const [accts, setAccts] = useState({ tiktok: '', instagram: '', youtube: '' });
  const [cookies, setCookies] = useState(!!cookiesConfigured);
  const [logoOn, setLogoOn] = useState(false);
  const [fonts, setFonts] = useState([]);
  const [provider, setProvider] = useState('deepgram');
  const [model, setModel] = useState('');
  const [models, setModels] = useState(FALLBACK_MODELS);
  const [loadingModels, setLoadingModels] = useState(false);

  // Pull the live model list from the backend (uses the saved key if the
  // header is empty). Merges discovery with the curated fallback + the
  // currently-selected model so the dropdown is never empty and never drops
  // the active choice.
  const loadModels = async (key) => {
    setLoadingModels(true);
    try {
      const { models: live } = await getModels(key || gemini || apiKey || '');
      const seen = new Set();
      const merged = [];
      [...(live || []), ...FALLBACK_MODELS].forEach((m) => {
        if (m?.name && !seen.has(m.name)) { seen.add(m.name); merged.push(m); }
      });
      if (merged.length) setModels(merged);
    } catch { /* keep fallback */ }
    finally { setLoadingModels(false); }
  };

  // Source of truth for "is this key set" is always the backend's response,
  // never the (optimistic) input text — refetched after every save/clear so
  // the badge can't drift from what's actually persisted.
  const refreshConfig = async () => {
    const c = await getConfig();
    if (!c) { pushToast?.('warn', t('settings.toast.keyStatusFailed')); return; }
    setPresent({
      gemini: !!c.GEMINI_API_KEY, hf: !!c.HF_TOKEN, deepgram: !!c.DEEPGRAM_API_KEY, elevenlabs: !!c.ELEVENLABS_API_KEY,
      twitchId: !!c.TWITCH_CLIENT_ID, twitchSecret: !!c.TWITCH_CLIENT_SECRET,
    });
    if (c.TRANSCRIPTION_PROVIDER) setProvider(c.TRANSCRIPTION_PROVIDER);
    if (c.GEMINI_MODEL) setModel(c.GEMINI_MODEL);
  };

  useEffect(() => {
    refreshConfig().then(loadModels);
    getZernio().then((z) => { setZernioState(z); if (z.accounts) setAccts({ tiktok: '', instagram: '', youtube: '', ...z.accounts }); }).catch(() => {});
    cookiesStatus().then((s) => setCookies(!!s.configured)).catch(() => {});
    logoStatus().then((s) => setLogoOn(!!s.configured)).catch(() => {});
    listFonts().then(({ fonts: f }) => setFonts(Array.isArray(f) ? f : [])).catch(() => {});
    // Mount-once bootstrap; loadModels reads the latest key via closure on call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const saveKeys = async (patch) => {
    try { await saveConfig(patch); pushToast?.('success', t('settings.toast.saved')); await refreshConfig(); }
    catch { pushToast?.('error', t('settings.toast.saveFailed')); }
  };

  const saveZernioCfg = async () => {
    try {
      const payload = { accounts: accts };
      if (zKey.trim()) payload.api_key = zKey.trim();
      const z = await saveZernio(payload);
      setZernioState(z); setZKey('');
      pushToast?.('success', t('settings.toast.zernioSaved'));
    } catch { pushToast?.('error', t('settings.toast.zernioSaveFailed')); }
  };

  const discover = async () => {
    try {
      // Discovery runs against the *saved* key, so persist a freshly-typed one
      // first — otherwise the backend 400s with "API key not configured".
      if (zKey.trim()) { await saveZernio({ api_key: zKey.trim(), accounts: accts }); setZKey(''); }
      const { accounts } = await discoverZernioAccounts();
      const next = { ...accts };
      (accounts || []).forEach((a) => {
        const p = (a.platform || '').toLowerCase();
        const id = a._id || a.id;
        if (p.includes('tiktok')) next.tiktok = id;
        else if (p.includes('insta')) next.instagram = id;
        else if (p.includes('you')) next.youtube = id;
      });
      setAccts(next);
      pushToast?.('success', t('settings.toast.discovered', { count: (accounts || []).length }));
    } catch { pushToast?.('error', t('settings.toast.discoverFailed')); }
  };

  const onCookieFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    try { await uploadCookies(f); setCookies(true); onCookiesChange?.(true); pushToast?.('success', t('settings.toast.cookiesUploaded')); }
    catch { pushToast?.('error', t('settings.toast.cookieUploadFailed')); }
  };
  const removeCookies = async () => {
    try { await deleteCookies(); setCookies(false); onCookiesChange?.(false); pushToast?.('info', t('settings.toast.cookiesRemoved')); }
    catch { pushToast?.('error', t('settings.toast.removeFailed')); }
  };

  const onLogoFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    try { await uploadLogo(f); setLogoOn(true); pushToast?.('success', t('settings.toast.logoUploaded')); }
    catch (err) { pushToast?.('error', String(err.message || t('settings.toast.logoUploadFailed')).slice(0, 80)); }
  };
  const removeLogo = async () => {
    try { await deleteLogo(); setLogoOn(false); pushToast?.('info', t('settings.toast.logoRemoved')); }
    catch { pushToast?.('error', t('settings.toast.removeFailed')); }
  };

  // Only user-uploaded faces are deletable; bundled ones are part of the app.
  const bundled = new Set(SUB_FONTS.map(([v]) => v));
  const onFontFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    try { const { fonts: nf } = await uploadFont(f); setFonts(nf || fonts); pushToast?.('success', t('settings.toast.fontAdded')); }
    catch (err) { pushToast?.('error', String(err.message || t('settings.toast.fontUploadFailed')).slice(0, 80)); }
  };
  const removeFont = async (name) => {
    try { const { fonts: nf } = await deleteFont(name); setFonts(nf || fonts.filter((n) => n !== name)); pushToast?.('info', t('settings.toast.fontRemoved')); }
    catch { pushToast?.('error', t('settings.toast.removeFailed')); }
  };

  return (
    <div className="container narrow fade-in">
      <Hero eyebrow={t('settings.hero.eyebrow')} line1={t('settings.hero.line1')} sub={t('settings.hero.sub')} />

      <Panel title={t('settings.apiKeys.title')} sub={t('settings.apiKeys.sub')} icon="key-round" style={{ marginBottom: 18 }}>
        <KeyRow icon="sparkles" name={t('settings.key.gemini.name')} desc={t('settings.key.gemini.desc')} value={gemini} present={present.gemini}
          onChange={(v) => { setGemini(v); onApiKey?.(v); }} onSave={() => saveKeys({ GEMINI_API_KEY: gemini })}
          onClear={() => { setGemini(''); onApiKey?.(''); saveKeys({ GEMINI_API_KEY: '' }); }} placeholder="AIza…" />
        <KeyRow icon="audio-lines" name={t('settings.key.deepgram.name')} desc={t('settings.key.deepgram.desc')} value={deepgram} present={present.deepgram}
          onChange={setDeepgram} onSave={() => saveKeys({ DEEPGRAM_API_KEY: deepgram })}
          onClear={() => { setDeepgram(''); saveKeys({ DEEPGRAM_API_KEY: '' }); }} placeholder="dg_…" />
        <KeyRow icon="audio-lines" name={t('settings.key.elevenlabs.name')} desc={t('settings.key.elevenlabs.desc')} value={elevenlabs} present={present.elevenlabs}
          onChange={setElevenlabs} onSave={() => saveKeys({ ELEVENLABS_API_KEY: elevenlabs })}
          onClear={() => { setElevenlabs(''); saveKeys({ ELEVENLABS_API_KEY: '' }); }} placeholder="sk_…" />
        <KeyRow icon="scan-face" name={t('settings.key.hf.name')} desc={t('settings.key.hf.desc')} value={hf} present={present.hf}
          onChange={setHf} onSave={() => saveKeys({ HF_TOKEN: hf })}
          onClear={() => { setHf(''); saveKeys({ HF_TOKEN: '' }); }} placeholder="hf_…" />
        <KeyRow icon="rss" name={t('settings.key.twitchId.name')} desc={t('settings.key.twitchId.desc')} value={twitchId} present={present.twitchId}
          onChange={setTwitchId} onSave={() => saveKeys({ TWITCH_CLIENT_ID: twitchId })}
          onClear={() => { setTwitchId(''); saveKeys({ TWITCH_CLIENT_ID: '' }); }} placeholder={t('settings.key.twitchId.placeholder')} />
        <KeyRow icon="rss" name={t('settings.key.twitchSecret.name')} desc={t('settings.key.twitchSecret.desc')} value={twitchSecret} present={present.twitchSecret}
          onChange={setTwitchSecret} onSave={() => saveKeys({ TWITCH_CLIENT_SECRET: twitchSecret })}
          onClear={() => { setTwitchSecret(''); saveKeys({ TWITCH_CLIENT_SECRET: '' }); }} placeholder={t('settings.key.twitchSecret.placeholder')} />
        <KeyRow icon="key-round" name={t('settings.key.apiToken.name')} desc={t('settings.key.apiToken.desc')} value={apiToken} present={!!getApiToken()}
          onChange={setApiTokenState} onSave={() => { setApiToken(apiToken); pushToast?.('success', apiToken.trim() ? t('settings.apiToken.saved') : t('settings.apiToken.cleared')); }} placeholder={t('settings.key.apiToken.placeholder')} />
        <div className="opt" style={{ borderBottom: 0 }}>
          <div className="oico"><Icon n="audio-lines" /></div>
          <div className="otxt"><div className="ot">{t('settings.transcription.title')}</div><div className="od">{t('settings.transcription.desc')}</div></div>
          <div className="r"><Segmented value={provider}
            onChange={(id) => { setProvider(id); saveKeys({ TRANSCRIPTION_PROVIDER: id }); }}
            options={[{ id: 'deepgram', label: 'Deepgram' }, { id: 'elevenlabs', label: 'ElevenLabs' }, { id: 'whisper', label: 'Whisper' }]} /></div>
        </div>
        {provider === 'deepgram' && !present.deepgram && (
          <div className="od" style={{ color: 'var(--warn, #f5a623)', padding: '0 0 8px 44px' }}>{t('settings.transcription.deepgramWarning')}</div>
        )}
        {provider === 'elevenlabs' && !present.elevenlabs && (
          <div className="od" style={{ color: 'var(--warn, #f5a623)', padding: '0 0 8px 44px' }}>{t('settings.transcription.elevenlabsWarning')}</div>
        )}
        <div className="opt" style={{ borderBottom: 0 }}>
          <div className="oico"><Icon n="sparkles" /></div>
          <div className="otxt"><div className="ot">{t('settings.geminiModel.title')}</div><div className="od">{t('settings.geminiModel.desc')}</div></div>
          <div className="r" style={{ gap: 8 }}>
            <select className="key-input" style={{ width: 'auto', minWidth: 200, fontFamily: 'var(--font-sans)' }}
              value={model}
              onChange={(e) => { setModel(e.target.value); saveKeys({ GEMINI_MODEL: e.target.value }); }}>
              {!model && <option value="">{t('settings.geminiModel.default')}</option>}
              {model && !models.some((m) => m.name === model) && <option value={model}>{model}</option>}
              {models.map((m) => <option key={m.name} value={m.name}>{m.display_name || m.name}</option>)}
            </select>
            <Btn variant="ghost" size="sm" icon="refresh-cw" onClick={() => loadModels()} disabled={loadingModels}>
              {loadingModels ? '…' : t('settings.refresh')}
            </Btn>
          </div>
        </div>
      </Panel>

      <Panel title={t('settings.publishing.title')} sub={t('settings.publishing.sub')} icon="send" style={{ marginBottom: 18 }}>
        <div className="zernio-card" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <div className="zico"><Icon n="rss" /></div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="kt">Zernio</div>
              <div className="kd">{zernio?.configured ? `${t('settings.zernio.connected')}${zernio.api_key_masked ? ' · ' + zernio.api_key_masked : ''}` : t('settings.zernio.connectedDesc')}</div>
            </div>
            {zernio?.configured && <span className="conn"><Icon n="circle-check" />{t('settings.zernio.connected')}</span>}
          </div>
          <input className="key-input" style={{ width: '100%' }} type="password" value={zKey}
            aria-label={t('settings.zernio.apiKeyAria')}
            placeholder={zernio?.configured ? t('settings.zernio.replaceKey') : t('settings.zernio.newKey')} onChange={(e) => setZKey(e.target.value)} />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 8 }}>
            {['tiktok', 'instagram', 'youtube'].map((p) => (
              <input key={p} className="key-input" style={{ width: '100%', fontFamily: 'var(--font-sans)' }}
                aria-label={`${p} account id`}
                value={accts[p] || ''} placeholder={`${p} account id`} onChange={(e) => setAccts((a) => ({ ...a, [p]: e.target.value }))} />
            ))}
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <Btn variant="secondary" size="sm" icon="rss" onClick={discover}>{t('settings.zernio.discover')}</Btn>
            <Btn variant="primary" size="sm" icon="check" onClick={saveZernioCfg}>{t('settings.save')}</Btn>
          </div>
        </div>
      </Panel>

      <Panel title={t('settings.brand.title')} sub={t('settings.brand.sub')} icon="stamp" style={{ marginBottom: 18 }}>
        <div className="opt">
          <div className="oico"><Icon n="image" /></div>
          <div className="otxt"><div className="ot">{t('settings.logo.title')}</div><div className="od">{logoOn ? t('settings.logo.descOn') : t('settings.logo.descOff')}</div></div>
          <div className="r" style={{ gap: 8 }}>
            <label className="btn btn-secondary btn-sm" style={{ cursor: 'pointer' }}>
              <Icon n="upload" />{t('settings.upload')}
              <input type="file" accept="image/png,.png" hidden onChange={onLogoFile} />
            </label>
            {logoOn && <Btn variant="ghost" size="sm" icon="trash-2" onClick={removeLogo}>{t('settings.remove')}</Btn>}
          </div>
        </div>
        <div className="opt" style={{ borderBottom: 0, alignItems: 'flex-start' }}>
          <div className="oico"><Icon n="baseline" /></div>
          <div className="otxt" style={{ flex: 1 }}>
            <div className="ot">{t('settings.fonts.title')}</div>
            <div className="od">{t('settings.fonts.desc')}</div>
            {fonts.filter((n) => !bundled.has(n)).length > 0 && (
              <div className="s-sub" style={{ marginTop: 10 }}>
                {fonts.filter((n) => !bundled.has(n)).map((n) => (
                  <span key={n} className="chip" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    {n}
                    <button type="button" className="mini" aria-label={t('settings.removeAria', { name: n })} title={t('settings.remove')} onClick={() => removeFont(n)}><Icon n="x" /></button>
                  </span>
                ))}
              </div>
            )}
          </div>
          <div className="r">
            <label className="btn btn-secondary btn-sm" style={{ cursor: 'pointer' }}>
              <Icon n="upload" />{t('settings.upload')}
              <input type="file" accept=".ttf,.otf,.ttc,font/ttf,font/otf" hidden onChange={onFontFile} />
            </label>
          </div>
        </div>
      </Panel>

      <Panel title={t('settings.downloads.title')} sub={t('settings.downloads.sub')} icon="cookie">
        <div className="opt" style={{ borderBottom: 0 }}>
          <div className="oico"><Icon n="cookie" /></div>
          <div className="otxt"><div className="ot">{t('settings.cookies.title')}</div><div className="od">{cookies ? t('settings.cookies.descOn') : t('settings.cookies.descOff')}</div></div>
          <div className="r" style={{ gap: 8 }}>
            <label className="btn btn-secondary btn-sm" style={{ cursor: 'pointer' }}>
              <Icon n="upload" />{t('settings.upload')}
              <input type="file" accept=".txt" hidden onChange={onCookieFile} />
            </label>
            {cookies && <Btn variant="ghost" size="sm" icon="trash-2" onClick={removeCookies}>{t('settings.remove')}</Btn>}
          </div>
        </div>
      </Panel>
    </div>
  );
}

export function ApiKeyModal({ onClose, onGoToSettings }) {
  const t = useT();
  const panelRef = useModalA11y(onClose);
  return (
    // Backdrop click is a mouse-only convenience; keyboard users close via
    // Esc (useModalA11y). currentTarget guard replaces stopPropagation.
    <div className="overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" ref={panelRef}
        role="dialog" aria-modal="true" aria-labelledby="apikey-modal-title">
        <div className="modal-head"><h3 id="apikey-modal-title">{t('apiKeyModal.title')}</h3><button className="x" onClick={onClose} aria-label={t('common.close')}><Icon n="x" /></button></div>
        <div className="modal-body">
          <p style={{ color: 'var(--fg-2)', fontSize: 14, lineHeight: 1.55 }}>
            {t('apiKeyModal.body')}
          </p>
        </div>
        <div className="modal-foot">
          <Btn variant="ghost" onClick={onClose}>{t('apiKeyModal.later')}</Btn>
          <div className="mf-right"><Btn variant="primary" icon="settings" onClick={onGoToSettings}>{t('apiKeyModal.openSettings')}</Btn></div>
        </div>
      </div>
    </div>
  );
}
