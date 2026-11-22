import { useState, useEffect, useRef } from 'react';
import { api } from './api';

const POLL_INTERVAL = 2000;

export function useTriageData() {
  const [metrics, setMetrics] = useState(null);
  const [windows, setWindows] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [simStatus, setSimStatus] = useState({ running: false, logs_per_second: 0, active_scenario: null });
  const [error, setError] = useState(null);
  const timerRef = useRef(null);

  async function fetchAll() {
    try {
      const [statusData, recentData] = await Promise.all([
        api.status(),
        api.recent(80, 20),
      ]);
      setMetrics(statusData.metrics);
      setSimStatus(statusData.simulator);
      setWindows(recentData.windows || []);
      setIncidents(recentData.incidents || []);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    fetchAll();
    timerRef.current = setInterval(fetchAll, POLL_INTERVAL);
    return () => clearInterval(timerRef.current);
  }, []);

  return { metrics, windows, incidents, simStatus, error, refresh: fetchAll };
}
