import { useState, useEffect } from 'react';
import type { AppSettings } from '../lib/types';

export function Settings() {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    window.fcdb.getSettings().then(setSettings);
  }, []);

  async function toggle(key: keyof AppSettings) {
    if (!settings) return;
    setSaving(true);
    const updated = await window.fcdb.setSetting(key, !settings[key]);
    setSettings(updated);
    setSaving(false);
  }

  if (!settings) return <div className="view"><div className="empty">Loading settings…</div></div>;

  return (
    <div className="view">
      <div className="view-header"><h2>Settings</h2></div>

      <div className="settings-list">
        <div className="setting-row">
          <div className="setting-info">
            <div className="setting-label">Auto-start backend on launch</div>
            <div className="setting-desc">
              Spawn the Python scheduler automatically when the app opens.
              Disable if you want to manage the backend process manually.
            </div>
          </div>
          <button
            className={`toggle-btn ${settings.autoStartBackend ? 'on' : 'off'}`}
            onClick={() => toggle('autoStartBackend')}
            disabled={saving}
          >
            {settings.autoStartBackend ? 'ON' : 'OFF'}
          </button>
        </div>

        <div className="setting-row">
          <div className="setting-info">
            <div className="setting-label">Backend process</div>
            <div className="setting-desc">Manually control the running backend.</div>
          </div>
          <div className="btn-group">
            <button className="btn" onClick={() => window.fcdb.restartBackend()}>Restart</button>
            <button className="btn btn-danger" onClick={() => window.fcdb.stopBackend()}>Stop</button>
          </div>
        </div>
      </div>
    </div>
  );
}
