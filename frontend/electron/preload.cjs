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
  askMultiModel:    (opts) => ipcRenderer.invoke('db:askMultiModel', opts),
  getProviderAvailability: () => ipcRenderer.invoke('db:getProviderAvailability'),
  getProviderHealth: () => ipcRenderer.invoke('db:getProviderHealth'),
  buildAskContext:   (opts) => ipcRenderer.invoke('db:buildAskContext', opts),
  callSingleProvider:(opts) => ipcRenderer.invoke('db:callSingleProvider', opts),
  logAskMulti:       (opts) => ipcRenderer.invoke('db:logAskMulti', opts),
  cancelSession:     (opts) => ipcRenderer.invoke('db:cancelSession', opts),
  // Push channel: main emits cold-start hints while a provider fetch is pending.
  // Returns an unsubscribe function so the renderer can clean up on unmount.
  onProviderStatus: (callback) => {
    const listener = (_e, data) => callback(data);
    ipcRenderer.on('provider-status', listener);
    return () => ipcRenderer.removeListener('provider-status', listener);
  },
  getRecommendations:            (opts) => ipcRenderer.invoke('db:getRecommendations', opts),
  dismissRecommendation:         (opts) => ipcRenderer.invoke('db:dismissRecommendation', opts),
  requestFreshPrice:             (opts) => ipcRenderer.invoke('db:requestFreshPrice', opts),
  getRecommendationStats:        (opts) => ipcRenderer.invoke('db:getRecommendationStats', opts),
  triggerRecommendations:        (opts) => ipcRenderer.invoke('db:triggerRecommendations', opts),
  getRecommendationBudgetStatus: ()     => ipcRenderer.invoke('db:getRecommendationBudgetStatus'),

  // Settings + backend control
  getSettings:    () => ipcRenderer.invoke('get-settings'),
  setSetting:     (key, value) => ipcRenderer.invoke('set-setting', key, value),
  restartBackend: () => ipcRenderer.invoke('restart-backend'),
  stopBackend:    () => ipcRenderer.invoke('stop-backend'),
  backendRunning: () => ipcRenderer.invoke('backend-running'),
});
