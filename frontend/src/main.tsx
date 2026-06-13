import './styles/app.css';
import React from 'react';
import { createRoot } from 'react-dom/client';
import { AppRoutes } from './pages/AppRoutes';

const root = document.getElementById('root');

if (!root) {
  throw new Error('missing root element');
}

createRoot(root).render(
  <React.StrictMode>
    <AppRoutes />
  </React.StrictMode>,
);
