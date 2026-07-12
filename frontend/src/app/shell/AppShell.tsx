import { Activity, FileText, Gauge, KeyRound, ScrollText, ServerCog, SquareTerminal } from 'lucide-react';
import { PropsWithChildren, ReactNode, useEffect } from 'react';
import { NavLink, useLocation } from 'react-router-dom';

type Identity = {
  instance_id: string;
  name: string;
  capabilities: string[];
};

const navigation = [
  { to: '/terminal', label: 'Terminal', capability: 'terminals.v1', icon: SquareTerminal },
  { to: '/services', label: 'Services', capability: 'services.v1', icon: ServerCog },
  { to: '/observations', label: 'Observations', capability: 'observations.v2', icon: Activity },
  { to: '/logs', label: 'Logs', capability: 'logs.v2', icon: FileText },
  { to: '/metrics', label: 'Metrics', capability: 'monitoring.snapshot.v1', icon: Gauge },
  { to: '/audit', label: 'Audit', capability: 'audit.v1', icon: ScrollText },
  { to: '/settings/manager-access', label: 'Manager Access', capability: 'runtime.v2', icon: KeyRound },
];

const managerNavigation = [
  { to: '/fleet', label: 'Fleet' },
  { to: '/monitoring', label: 'Monitoring' },
  { to: '/audit', label: 'Audit' },
];

export function AppShell({ identity, terminal, children, manager = false }: PropsWithChildren<{ identity: Identity; terminal: ReactNode; manager?: boolean }>) {
  const location = useLocation();

  useEffect(() => {
    if (manager) return;
    const focusHeading = () => {
      const heading = document.querySelector<HTMLElement>('#main-content h1:not(.sr-only)');
      if (heading) {
        heading.focus();
        return true;
      }
      return false;
    };
    if (location.pathname === '/terminal') {
      document.querySelector<HTMLElement>('.persistent-terminal h1')?.focus();
      return;
    }
    if (focusHeading()) {
      return;
    }
    const observer = new MutationObserver(() => {
      if (focusHeading()) {
        observer.disconnect();
      }
    });
    observer.observe(document.getElementById('main-content') ?? document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [location.pathname, manager]);

  return (
    <div className="app-frame">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <aside className="app-sidebar">
        <div className="product-lockup"><span className="product-mark" aria-hidden="true">IG</span><span>IC Env Guard</span></div>
        <nav aria-label={manager ? 'Manager navigation' : 'Standalone navigation'}>
          {manager ? managerNavigation.map(({ to, label }) => (
            <NavLink key={to} to={to} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
              <span>{label}</span>
            </NavLink>
          )) : navigation.map(({ to, label, capability, icon: Icon }) => {
            const available = identity.capabilities.includes(capability);
            const reason = available ? undefined : `Unavailable: requires ${capability}`;
            return available ? (
              <NavLink key={to} to={to} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`} aria-label={label}>
                <Icon size={18} aria-hidden="true" /><span>{label}</span>
              </NavLink>
            ) : (
              <a key={to} href={to} className="nav-item disabled" aria-disabled="true" title={reason} onClick={(event) => event.preventDefault()}>
                <Icon size={18} aria-hidden="true" /><span>{label}</span><span className="sr-only"> — {reason}</span>
              </a>
            );
          })}
        </nav>
      </aside>
      <div className="app-workspace">
        <header className="identity-bar">
          <div><span className="mode-label">{manager ? 'Manager Console' : 'Standalone Agent'}</span><strong>{identity.name}</strong></div>
          {manager ? null : <code>{identity.instance_id}</code>}
        </header>
        <main id="main-content" tabIndex={-1}>
          {!manager ? <div hidden={location.pathname !== '/terminal'} className="persistent-terminal">
            <h1 tabIndex={-1} className="sr-only">Terminal</h1>
            {terminal}
          </div> : null}
          {!manager && location.pathname === '/terminal' ? null : children}
        </main>
      </div>
    </div>
  );
}
