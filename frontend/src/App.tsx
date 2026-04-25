import { useState, Component } from 'react';
import type { ReactNode, ErrorInfo } from 'react';
import { TopMovers } from './views/TopMovers';
import { CardSearch } from './views/CardSearch';
import { ScraperHealth } from './views/ScraperHealth';
import { Signals } from './views/Signals';
import { Settings } from './views/Settings';
import { Fodder } from './views/Fodder';
import { Ask } from './views/Ask';
import { usePlatform } from './lib/usePlatform';
import './App.css';

type View = 'ask' | 'top-movers' | 'card-search' | 'fodder' | 'scraper-health' | 'signals' | 'settings';

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, color: '#f87171', fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
          <strong>Render error:</strong>{'\n'}{this.state.error.stack}
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const [view, setView] = useState<View>('ask');
  const { platform, setPlatform } = usePlatform();

  return (
    <ErrorBoundary>
      <div className="shell">
        <aside className="sidebar">
          <div className="logo">FCPriceMaster</div>

          <nav className="nav">
            <NavItem label="Ask" active={view === 'ask'} onClick={() => setView('ask')} />
            <NavItem label="Top Movers" active={view === 'top-movers'} onClick={() => setView('top-movers')} />
            <NavItem label="Card Search" active={view === 'card-search'} onClick={() => setView('card-search')} />
            <NavItem label="Fodder" active={view === 'fodder'} onClick={() => setView('fodder')} />
            <NavItem label="Signals" active={view === 'signals'} onClick={() => setView('signals')} />
            <NavItem label="Scraper Health" active={view === 'scraper-health'} onClick={() => setView('scraper-health')} />
            <NavItem label="Settings" active={view === 'settings'} onClick={() => setView('settings')} />
          </nav>

          <div className="platform-toggle">
            <button
              className={`plat-btn ${platform === 'pc' ? 'active' : ''}`}
              onClick={() => setPlatform('pc')}
            >PC</button>
            <button
              className={`plat-btn ${platform === 'console' ? 'active' : ''}`}
              onClick={() => setPlatform('console')}
            >Console</button>
          </div>
        </aside>

        <main className="content">
          {view === 'ask'            && <Ask platform={platform} setPlatform={setPlatform} />}
          {view === 'top-movers'     && <TopMovers platform={platform} />}
          {view === 'card-search'    && <CardSearch platform={platform} />}
          {view === 'fodder'         && <Fodder platform={platform} />}
          {view === 'scraper-health' && <ScraperHealth />}
          {view === 'signals'        && <Signals />}
          {view === 'settings'       && <Settings />}
        </main>
      </div>
    </ErrorBoundary>
  );
}

function NavItem({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button className={`nav-item ${active ? 'active' : ''}`} onClick={onClick}>
      {label}
    </button>
  );
}
