// Minimal i18n: a flat key → string dictionary per language, a context that
// exposes t()/lang/setLang, and localStorage persistence. No external library —
// the dashboard has ~30 component test files that render pieces of the app
// bare (no providers at all), and a real library's setup would need every one
// of them touched just to keep rendering.
//
// The DEFAULT context value (used by any component NOT wrapped in
// <I18nProvider>) resolves against English — byte-for-byte the same strings
// that used to be hardcoded. That is what keeps every existing test passing
// unmodified: they render components directly, so they get the default
// value, which behaves exactly like the old hardcoded text. Only the real
// app (main.jsx wraps <RedesignApp> in <I18nProvider>) can switch language.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { en } from './en.js';
import { de } from './de.js';

export const DICTIONARIES = { en, de };
export const LANGUAGES = [
  { id: 'en', label: 'EN' },
  { id: 'de', label: 'DE' },
];
const STORAGE_KEY = 'clippyme_lang';

// `key` doubles as the English fallback text at every call site (translate()
// falls back to the key itself when even the English dictionary is missing
// it), so a typo'd or not-yet-translated key degrades to readable text
// instead of a blank label.
function translate(lang, key, vars) {
  const dict = DICTIONARIES[lang] || DICTIONARIES.en;
  let str = dict[key] ?? DICTIONARIES.en[key] ?? key;
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      str = str.replaceAll(`{${name}}`, String(value));
    }
  }
  return str;
}

const DEFAULT_CONTEXT = {
  lang: 'en',
  setLang: () => {},
  t: (key, vars) => translate('en', key, vars),
};

const I18nContext = createContext(DEFAULT_CONTEXT);

function detectInitialLang() {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && DICTIONARIES[saved]) return saved;
  } catch { /* localStorage unavailable (SSR, some test environments) */ }
  return 'en';
}

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(detectInitialLang);

  useEffect(() => {
    try { window.localStorage.setItem(STORAGE_KEY, lang); } catch { /* best-effort */ }
  }, [lang]);

  const setLang = useCallback((next) => {
    if (DICTIONARIES[next]) setLangState(next);
  }, []);

  const t = useCallback((key, vars) => translate(lang, key, vars), [lang]);

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}

export function useT() {
  return useContext(I18nContext).t;
}
