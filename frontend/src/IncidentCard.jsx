import React from 'react';

const SEVERITY_CONFIG = {
  CRITICAL: { bg: 'bg-red-900/40', border: 'border-red-500', text: 'text-red-400', badge: 'bg-red-500' },
  HIGH:     { bg: 'bg-orange-900/30', border: 'border-orange-500', text: 'text-orange-400', badge: 'bg-orange-500' },
  MEDIUM:   { bg: 'bg-yellow-900/30', border: 'border-yellow-500', text: 'text-yellow-400', badge: 'bg-yellow-500' },
  LOW:      { bg: 'bg-blue-900/20', border: 'border-blue-500', text: 'text-blue-400', badge: 'bg-blue-500' },
};

function SeverityBadge({ severity }) {
  const cfg = SEVERITY_CONFIG[severity] || SEVERITY_CONFIG.LOW;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-bold text-white ${cfg.badge}`}>
      {severity}
    </span>
  );
}

function ConfidenceBar({ score }) {
  const pct = Math.round((score || 0) * 100);
  const color = pct >= 80 ? 'bg-tg-red' : pct >= 60 ? 'bg-tg-orange' : 'bg-tg-yellow';
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 bg-tg-border rounded-full h-1.5">
        <div className={`${color} h-1.5 rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-tg-muted font-mono">{pct}%</span>
    </div>
  );
}

export function IncidentCard({ incident, index }) {
  const [expanded, setExpanded] = React.useState(index === 0);
  const severity = incident.severity || 'LOW';
  const cfg = SEVERITY_CONFIG[severity] || SEVERITY_CONFIG.LOW;

  if (incident.was_deduplicated) {
    return (
      <div className="border border-tg-border rounded-lg p-3 bg-tg-surface/50 opacity-60">
        <div className="flex items-center gap-2 text-tg-muted text-sm">
          <span className="text-lg">🔇</span>
          <span>Duplicate suppressed — <span className="font-mono">{incident.affected_service}</span></span>
          <span className="ml-auto text-xs">{new Date(incident.created_at).toLocaleTimeString()}</span>
        </div>
      </div>
    );
  }

  return (
    <div
      className={`border ${cfg.border} rounded-lg ${cfg.bg} cursor-pointer transition-all`}
      onClick={() => setExpanded(e => !e)}
    >
      {/* Header */}
      <div className="flex items-center gap-3 p-4">
        <SeverityBadge severity={severity} />
        <span className="font-mono text-sm text-tg-text font-semibold">{incident.affected_service}</span>
        {incident.is_mock && (
          <span className="text-xs bg-purple-900/50 text-purple-300 border border-purple-700 px-1.5 py-0.5 rounded">
            Mock LLM
          </span>
        )}
        <span className="ml-auto text-xs text-tg-muted">
          {new Date(incident.created_at).toLocaleTimeString()}
        </span>
        <span className="text-tg-muted text-xs">{expanded ? '▲' : '▼'}</span>
      </div>

      {expanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-tg-border/50 pt-3">
          {/* Tier-1 scores */}
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div className="bg-tg-bg/60 rounded p-2">
              <div className="text-tg-muted mb-1">Tier-1 Latency</div>
              <div className="font-mono text-tg-text">
                {/* window_id reference */}
                — ms
              </div>
            </div>
            <div className="bg-tg-bg/60 rounded p-2">
              <div className="text-tg-muted mb-1">LLM Latency</div>
              <div className="font-mono text-tg-text">{incident.llm_latency_ms?.toFixed(0) ?? '—'}ms</div>
            </div>
          </div>

          {/* Root cause */}
          <div>
            <div className="text-xs text-tg-muted uppercase tracking-wider mb-1">Root Cause</div>
            <p className={`text-sm ${cfg.text} leading-relaxed`}>{incident.root_cause || '—'}</p>
          </div>

          {/* Confidence */}
          <div>
            <div className="text-xs text-tg-muted uppercase tracking-wider mb-1">
              Confidence Score
            </div>
            <ConfidenceBar score={incident.confidence_score} />
          </div>

          {/* Remediation */}
          {incident.suggested_remediation?.length > 0 && (
            <div>
              <div className="text-xs text-tg-muted uppercase tracking-wider mb-2">
                Suggested Remediation
              </div>
              <ol className="space-y-1.5">
                {incident.suggested_remediation.map((action, i) => (
                  <li key={i} className="flex gap-2 text-sm text-tg-text">
                    <span className="text-tg-green font-bold shrink-0">{i + 1}.</span>
                    <span>{action}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* Model info */}
          <div className="text-xs text-tg-muted font-mono border-t border-tg-border/50 pt-2">
            Model: {incident.llm_model_used || '—'} · ID: #{incident.id}
          </div>
        </div>
      )}
    </div>
  );
}
