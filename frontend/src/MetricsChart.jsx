import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceDot,
  Legend,
} from 'recharts';

const CustomTooltip = ({ active, payload, label }) => {
  if (active && payload?.length) {
    return (
      <div className="bg-tg-surface border border-tg-border rounded-lg p-3 text-xs font-mono shadow-xl">
        <p className="text-tg-muted mb-1">{label}</p>
        {payload.map((p) => (
          <p key={p.dataKey} style={{ color: p.color }}>
            {p.name}: {typeof p.value === 'number' ? p.value.toFixed(2) : p.value}
          </p>
        ))}
      </div>
    );
  }
  return null;
};

export function MetricsChart({ windows }) {
  const data = windows.map((w) => ({
    time: new Date(w.window_start).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    p95: Math.round(w.p95_latency_ms),
    errorRate: parseFloat((w.error_rate * 100).toFixed(1)),
    isAnomaly: w.is_anomaly,
  }));

  return (
    <div className="bg-tg-surface border border-tg-border rounded-xl p-4">
      <h3 className="text-sm font-semibold text-tg-muted uppercase tracking-wider mb-4">
        Live Metrics — P95 Latency & Error Rate
      </h3>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
          <XAxis
            dataKey="time"
            tick={{ fill: '#8b949e', fontSize: 11, fontFamily: 'monospace' }}
            tickLine={false}
            axisLine={{ stroke: '#30363d' }}
            interval="preserveStartEnd"
          />
          <YAxis
            yAxisId="latency"
            orientation="left"
            tick={{ fill: '#58a6ff', fontSize: 11, fontFamily: 'monospace' }}
            tickLine={false}
            axisLine={false}
            label={{ value: 'P95 (ms)', angle: -90, position: 'insideLeft', fill: '#58a6ff', fontSize: 11, dx: -5 }}
          />
          <YAxis
            yAxisId="error"
            orientation="right"
            tick={{ fill: '#f85149', fontSize: 11, fontFamily: 'monospace' }}
            tickLine={false}
            axisLine={false}
            domain={[0, 100]}
            label={{ value: 'Error %', angle: 90, position: 'insideRight', fill: '#f85149', fontSize: 11, dx: 10 }}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: '11px', color: '#8b949e' }}
            formatter={(value) => <span style={{ color: '#8b949e' }}>{value}</span>}
          />
          <Line
            yAxisId="latency"
            type="monotone"
            dataKey="p95"
            name="P95 Latency (ms)"
            stroke="#58a6ff"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: '#58a6ff' }}
          />
          <Line
            yAxisId="error"
            type="monotone"
            dataKey="errorRate"
            name="Error Rate (%)"
            stroke="#f85149"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: '#f85149' }}
          />
          {/* Anomaly markers */}
          {data
            .filter((d) => d.isAnomaly)
            .map((d, i) => (
              <ReferenceDot
                key={i}
                x={d.time}
                y={d.p95}
                yAxisId="latency"
                r={6}
                fill="#f85149"
                stroke="#fff"
                strokeWidth={1}
              />
            ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
