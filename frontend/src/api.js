const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

async function req(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json();
}

export const api = {
  health: () => req('/health'),
  status: () => req('/status'),
  metrics: () => req('/metrics'),
  recent: (wl = 60, il = 15) => req(`/recent?window_limit=${wl}&incident_limit=${il}`),
  incidents: (limit = 20) => req(`/incidents?limit=${limit}`),
  windows: (limit = 60) => req(`/windows?limit=${limit}`),
  startSimulator: () => req('/simulator/start', { method: 'POST' }),
  stopSimulator: () => req('/simulator/stop', { method: 'POST' }),
  injectIncident: (scenario, duration = 30) =>
    req('/inject', {
      method: 'POST',
      body: JSON.stringify({ scenario, duration_seconds: duration }),
    }),
  reset: () => req('/reset', { method: 'DELETE' }),
};
