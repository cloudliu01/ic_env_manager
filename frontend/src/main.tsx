import './styles/app.css';
import './shared/styles/tokens.css';
import './shared/styles/base.css';
import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App';

const root = document.getElementById('root');

if (!root) {
  throw new Error('missing root element');
}

createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
