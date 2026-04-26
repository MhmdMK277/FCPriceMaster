const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('fcdb', {
  // DB queries — main process owns the DB; preload relays via IPC
  getTopMovers:     (opts) => ipcRenderer.invoke('db:getTopMovers', opts),
  searchCards:      (opts) => ipcRenderer.invoke('db:searchCards', opts),
  getCardDetail:    (opts) => ipcRenderer.invoke('db:getCardDetail', opts),
  getScraperHealth: (opts) => ipcRenderer.invoke('db:getScraperHealth', opts),
  getRecentSignals: (opts) => ipcRenderer.invoke('db:getRecentSignals', opts),
  getFodderSummary:  (opts) => ipcRenderer.invoke('db:getFodderSummary', opts),
  getFodderSnapshot: (opts) => ipcRenderer.invoke('db:getFodderSnapshot', opts),
  getFodderByRating: (opts) => ipcRenderer.invoke('db:getFodderByRating', opts),
  getFodderHistory:  (opts) => ipcRenderer.invoke('db:getFodderHistory', opts),
  getLLMHistory:    (opts) => ipcRenderer.invoke('db:getLLMHistory', opts),
  askLLM:           (opts) => ipcRenderer.invoke('db:askLLM', opts),
  getRecommendations:     (opts) => ipcRenderer.invoke('db:getRecommendations', opts),
  dismissRecommendation:  (opts) => ipcRenderer.invoke('db:dismissRecommendation', opts),
  getRecommendationStats: (opts) => ipcRenderer.invoke('db:getRecommendationStats', opts),
  triggerRecommendations: (opts) => ipcRenderer.invoke('db:triggerRecommendations', opts),

  // Settings + backend control
  getSettings:    () => ipcRenderer.invoke('get-settings'),
  setSetting:     (key, value) => ipcRenderer.invoke('set-setting', key, value),
  restartBackend: () => ipcRenderer.invoke('restart-backend'),
  stopBackend:    () => ipcRenderer.invoke('stop-backend'),
  backendRunning: () => ipcRenderer.invoke('backend-running'),
});
