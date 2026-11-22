import { useState } from 'react';
import { Activity, Zap, DollarSign, AlertTriangle, Play, Square, RefreshCw } from 'lucide-react';
import { useTriageData } from './useTriageData';
import { KpiCard } from './KpiCard';
import { MetricsChart } from './MetricsChart';
import { IncidentCard } from './IncidentCard';
import { api } from './api';
import './index.css';

const SCENARIOS = [
  {
    id: 'db_pool_exhaustion',
    label: 'DB Pool Exhaustion',
    emoji: '🗄️',
    color: 'border-red-600 hover:bg-red-900/30 text-red-400',
    desc: 'High latency + connection timeouts',
  },
  {
    id: 'auth_502_spike',
    label: 'Auth 502 Spike',
    emoji: '🔐',
    color: 'border-orange-600 hover:bg-orange-900/30 text-orange-400',
    desc: 'Sudden HTTP 502/503 error surge',
  },
  {
    id: 'memory_leak_oom',
    label: 'Memory Leak OOM',
    emoji: '💀',
    color: 'border-purple-600 hover:bg-purple-900/30 text-purple-400',
    desc: 'Gradual latency creep → OOMKill',
  },
];

function StatusDot({ active }) {
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${active ? 'bg-tg-green animate-pulse' : 'bg-tg-muted'}`} />
  );
}

export default function App() {
  const { metrics, windows, incidents, simStatus, error, refresh } = useTriageData();
  const [injecting, setInjecting] = useState(null);
  const [togglingStream, setTogglingStream] = useState(false);
  const [notification, setNotification] = useState(null);

  function notify(msg, type = 'success') {
    setNotification({ msg, type });
    setTimeout(() => setNotification(null), 3500);
  }

  async function toggleStream() {
    setTogglingStream(true);
    try {
      if (simStatus.running) {
        await api.stopSimulator();
        notify('Stream stopped');
      } else {
        await api.startSimulator();
        notify('Stream started');
      }
      await refresh();
    } catch (e) {
      notify(e.message, 'error');
    }
    setTogglingStream(false);
  }

  async function inject(scenario) {
    setInjecting(scenario);
    try {
      await api.injectIncident(scenario, 40);
      notify(`Injected: ${scenario.replace(/_/g, ' ')}`);
    } catch (e) {
      notify(e.message, 'error');
    }
    setTimeout(() => setInjecting(null), 1000);
  }

  async function reset() {
    if (!window.confirm('Reset all data? This clears the DB and metrics.')) return;
    await api.reset();
    notify('Reset complete');
    refresh();
  }

  const activeIncidents = incidents.filter((i) => !i.was_deduplicated && i.severity);
  const costSaved = metrics?.estimated_cost_saved_usd ?? 0;
  const costPct = metrics?.cost_reduction_percent ?? 0;

  return (
    <div className="min-h-screen bg-tg-bg text-tg-text">
      {/* Top notification */}
      {notification && (
        <div
          className={`fixed top-4 right-4 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg transition-all
            ${notification.type === 'error' ? 'bg-red-900 border border-red-600 text-red-200' : 'bg-green-900 border border-green-600 text-green-200'}`}
        >
          {notification.msg}
        </div>
      )}

      {/* Header */}
      <header className="border-b border-tg-border bg-tg-surface px-6 py-4 flex items-center gap-4">
        <div className="flex items-center gap-3">
          <span className="text-2xl">🛡️</span>
          <div>
            <h1 className="text-lg font-bold text-tg-text tracking-tight">TriageGate</h1>
            <p className="text-xs text-tg-muted">Cost-Aware Log Anomaly Detection</p>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-3">
          {error && (
            <span className="text-xs text-red-400 font-mono bg-red-900/30 px-2 py-1 rounded border border-red-800">
              ⚠ API error: {error}
            </span>
          )}

          {/* Stream toggle */}
          <button
            onClick={toggleStream}
            disabled={togglingStream}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold border transition-all
              ${simStatus.running
                ? 'border-tg-green text-tg-green hover:bg-green-900/20'
                : 'border-tg-muted text-tg-muted hover:bg-tg-border'}`}
          >
            <StatusDot active={simStatus.running} />
            {simStatus.running ? (
              <><Square size={14} /> Stop Stream</>
            ) : (
              <><Play size={14} /> Start Stream</>
            )}
          </button>

          {simStatus.running && (
            <span className="text-xs text-tg-muted font-mono">
              {simStatus.logs_per_second.toFixed(1)} logs/s
            </span>
          )}

          <button
            onClick={refresh}
            className="p-2 rounded-lg border border-tg-border text-tg-muted hover:text-tg-text hover:border-tg-blue transition-all"
            title="Refresh"
          >
            <RefreshCw size={14} />
          </button>

          <button
            onClick={reset}
            className="text-xs text-tg-muted hover:text-red-400 transition-colors px-2 py-1 rounded border border-tg-border hover:border-red-800"
          >
            Reset
          </button>
        </div>
      </header>

      <main className="max-w-screen-2xl mx-auto px-6 py-6 space-y-6">
        {/* KPI Bar */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <KpiCard
            icon={<Activity size={14} />}
            label="Logs Processed"
            value={metrics?.total_logs_processed?.toLocaleString() ?? '0'}
            sub={`${metrics?.windows_evaluated ?? 0} windows evaluated`}
            color="text-tg-blue"
          />
          <KpiCard
            icon={<AlertTriangle size={14} />}
            label="Active Incidents"
            value={activeIncidents.length}
            sub={`${metrics?.anomalies_detected ?? 0} anomalies total`}
            color="text-tg-red"
          />
          <KpiCard
            icon={<Zap size={14} />}
            label="LLM Calls Avoided"
            value={metrics?.llm_calls_avoided?.toLocaleString() ?? '0'}
            sub={`${metrics?.llm_calls_made ?? 0} actual calls made`}
            color="text-tg-green"
          />
          <KpiCard
            icon={<DollarSign size={14} />}
            label="Cost Saved"
            value={`${costPct.toFixed(1)}%`}
            sub={`≈ \$${costSaved.toFixed(4)} USD saved`}
            color="text-tg-yellow"
          />
        </div>

        {/* Scenario Injection Panel */}
        <div className="bg-tg-surface border border-tg-border rounded-xl p-5">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-sm font-semibold text-tg-muted uppercase tracking-wider">
              💉 Inject Failure Scenario
            </span>
            {simStatus.active_scenario && (
              <span className="ml-2 text-xs bg-red-900/50 text-red-300 border border-red-700 px-2 py-0.5 rounded-full animate-pulse">
                Active: {simStatus.active_scenario.replace(/_/g, ' ')}
              </span>
            )}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {SCENARIOS.map((s) => (
              <button
                key={s.id}
                onClick={() => inject(s.id)}
                disabled={injecting === s.id}
                className={`flex flex-col gap-1 p-4 rounded-lg border text-left transition-all
                  ${s.color} ${injecting === s.id ? 'opacity-50 cursor-wait' : 'cursor-pointer'}`}
              >
                <div className="text-2xl">{s.emoji}</div>
                <div className="font-semibold text-sm">{s.label}</div>
                <div className="text-xs opacity-70">{s.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Two-column layout: chart + incidents */}
        <div className="grid grid-cols-1 xl:grid-cols-5 gap-6">
          {/* Chart takes 3/5 */}
          <div className="xl:col-span-3 space-y-4">
            <MetricsChart windows={windows} />

            {/* Cost breakdown */}
            <div className="bg-tg-surface border border-tg-border rounded-xl p-4">
              <h3 className="text-xs font-semibold text-tg-muted uppercase tracking-wider mb-3">
                💰 Cost Gate Breakdown
              </h3>
              <div className="grid grid-cols-3 gap-3 text-center">
                <div className="bg-tg-bg rounded-lg p-3">
                  <div className="text-xs text-tg-muted">Windows Healthy</div>
                  <div className="text-xl font-mono font-bold text-tg-green">
                    {Math.max(0, (metrics?.windows_evaluated ?? 0) - (metrics?.anomalies_detected ?? 0))}
                  </div>
                  <div className="text-xs text-tg-muted">→ No LLM</div>
                </div>
                <div className="bg-tg-bg rounded-lg p-3">
                  <div className="text-xs text-tg-muted">Dedup Suppressed</div>
                  <div className="text-xl font-mono font-bold text-tg-yellow">
                    {metrics?.duplicate_suppressed_count ?? 0}
                  </div>
                  <div className="text-xs text-tg-muted">→ Cooldown skip</div>
                </div>
                <div className="bg-tg-bg rounded-lg p-3">
                  <div className="text-xs text-tg-muted">LLM Called</div>
                  <div className="text-xl font-mono font-bold text-tg-red">
                    {metrics?.llm_calls_made ?? 0}
                  </div>
                  <div className="text-xs text-tg-muted">→ Triage triggered</div>
                </div>
              </div>

              {/* Cost bar */}
              {(metrics?.windows_evaluated ?? 0) > 0 && (
                <div className="mt-4">
                  <div className="flex justify-between text-xs text-tg-muted mb-1">
                    <span>Naive (all windows → LLM)</span>
                    <span>TriageGate actual</span>
                  </div>
                  <div className="relative h-4 bg-tg-border rounded-full overflow-hidden">
                    <div
                      className="h-full bg-tg-red/50 absolute inset-0 rounded-full"
                    />
                    <div
                      className="h-full bg-tg-green rounded-full transition-all"
                      style={{ width: `${100 - costPct}%` }}
                    />
                  </div>
                  <div className="flex justify-between text-xs mt-1">
                    <span className="text-tg-red">{metrics?.windows_evaluated} calls (naive)</span>
                    <span className="text-tg-green">{metrics?.llm_calls_made} calls ({(100 - costPct).toFixed(1)}% of naive)</span>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Incident feed takes 2/5 */}
          <div className="xl:col-span-2">
            <div className="bg-tg-surface border border-tg-border rounded-xl p-4 h-full">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-xs font-semibold text-tg-muted uppercase tracking-wider">
                  🚨 Incident Triage Feed
                </h3>
                <span className="text-xs text-tg-muted font-mono">
                  {incidents.length} events
                </span>
              </div>

              {incidents.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-48 text-tg-muted">
                  <span className="text-4xl mb-3">✅</span>
                  <p className="text-sm">No anomalies detected yet.</p>
                  <p className="text-xs mt-1 opacity-60">Start the stream and inject a scenario.</p>
                </div>
              ) : (
                <div className="space-y-3 overflow-y-auto max-h-[600px] pr-1">
                  {[...incidents].reverse().map((inc, i) => (
                    <IncidentCard key={inc.id} incident={inc} index={i} />
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Architecture note */}
        <div className="bg-tg-surface border border-tg-border rounded-xl p-4 text-xs text-tg-muted font-mono">
          <span className="text-tg-blue">Architecture: </span>
          Log Stream
          <span className="text-tg-muted mx-2">→</span>
          <span className="text-tg-green">Tier-1 (IsolationForest + Heuristics)</span>
          <span className="text-tg-muted mx-2">→</span>
          <span className="text-tg-red">Anomaly?</span>
          <span className="text-tg-muted mx-2">→</span>
          <span className="text-tg-yellow">Dedup Gate (60s cooldown)</span>
          <span className="text-tg-muted mx-2">→</span>
          <span className="text-tg-purple">Tier-2 LLM Triage</span>
          <span className="text-tg-muted mx-2">→</span>
          Structured RCA
        </div>
      </main>
    </div>
  );
}
