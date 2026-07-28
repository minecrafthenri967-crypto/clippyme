
import { useEffect, useState } from 'react';
import { Icon } from './icon';
import logoMark from './logo-mark.png';
import { useI18n } from '../i18n/context.jsx';

const TAB_KEYS = [
  { id: 'create', key: 'nav.create', icon: 'wand-sparkles' },
  { id: 'live', key: 'nav.live', icon: 'rss' },
  { id: 'history', key: 'nav.history', icon: 'clock' },
  { id: 'settings', key: 'nav.settings', icon: 'settings' },
];

function useBrowserOnline() {
  const [online, setOnline] = useState(() => typeof navigator === 'undefined' || navigator.onLine !== false);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine !== false);
    window.addEventListener('online', update);
    window.addEventListener('offline', update);
    return () => { window.removeEventListener('online', update); window.removeEventListener('offline', update); };
  }, []);
  return online;
}

// EN/DE pills. Only rendered inside the real app (I18nProvider gives setLang
// real effect); in a bare test render it still works, it just has nothing
// wired up to persist the choice beyond this component's lifetime.
function LanguageSwitch() {
  const { lang, setLang } = useI18n();
  return (
    <div className="lang-switch" role="group" aria-label="Language">
      {[['en', 'EN'], ['de', 'DE']].map(([id, label]) => (
        <button key={id} type="button" className={`lang-pill${lang === id ? ' active' : ''}`}
          aria-pressed={lang === id} onClick={() => setLang(id)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export function TopNav({ tab, setTab, busy }) {
  const online = useBrowserOnline();
  const { t } = useI18n();
  const status = !online ? t('nav.status.offline') : busy ? t('nav.status.working') : t('nav.status.local');
  return (
    <header className="topnav">
      <div className="brand" aria-label={t('nav.brand.aria')}>
        <img src={logoMark} alt="" aria-hidden="true" />
        <span>Clippy<span className="me">Me</span></span>
      </div>
      <nav className="tabs" aria-label={t('nav.primary.aria')}>
        {TAB_KEYS.map((item) => (
          <button key={item.id} type="button" className={`tab${tab === item.id ? ' active' : ''}`}
            aria-current={tab === item.id ? 'page' : undefined} onClick={() => setTab(item.id)}>
            <Icon n={item.icon} /><span className="lbl">{t(item.key)}</span>
          </button>
        ))}
      </nav>
      <div className="nav-right">
        <LanguageSwitch />
        <span className={`status-dot${online ? '' : ' offline'}`} role="status" aria-live="polite">
          <i aria-hidden="true" style={busy && online ? { background: 'var(--brand-blue)', boxShadow: '0 0 0 3px rgba(10,129,217,.16)' } : null} />
          <span className="sd-lbl">{status}</span>
        </span>
        <div className="avatar" aria-hidden="true">CM</div>
      </div>
    </header>
  );
}

export function Hero({ eyebrow, line1, grad, sub }) {
  return (
    <div className="hero">
      {eyebrow && <div className="eyebrow"><i aria-hidden="true" />{eyebrow}</div>}
      <h1>{line1}{grad && <> <span className="grad">{grad}</span></>}</h1>
      {sub && <p>{sub}</p>}
    </div>
  );
}
