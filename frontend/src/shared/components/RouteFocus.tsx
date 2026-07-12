import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';

export function RouteFocus() {
  const location = useLocation();

  useEffect(() => {
    const focusHeading = () => {
      const heading = document.querySelector<HTMLElement>('#main-content h1:not(.sr-only)');
      if (!heading) return false;
      heading.focus();
      return true;
    };
    if (focusHeading()) return;

    const observer = new MutationObserver(() => {
      if (focusHeading()) observer.disconnect();
    });
    observer.observe(document.getElementById('main-content') ?? document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [location.pathname]);

  return null;
}
