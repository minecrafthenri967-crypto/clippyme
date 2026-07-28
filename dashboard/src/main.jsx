
import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import RedesignApp from './redesign/RedesignApp';
import { AppErrorBoundary } from './redesign/AppErrorBoundary';
import { I18nProvider } from './i18n/context.jsx';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <I18nProvider>
      <AppErrorBoundary><RedesignApp /></AppErrorBoundary>
    </I18nProvider>
  </React.StrictMode>,
);
