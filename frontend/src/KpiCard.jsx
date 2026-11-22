export function KpiCard({ icon, label, value, sub, color = 'text-tg-blue' }) {
  return (
    <div className="bg-tg-surface border border-tg-border rounded-xl p-4 flex flex-col gap-1">
      <div className="flex items-center gap-2 text-tg-muted text-xs uppercase tracking-wider">
        <span>{icon}</span>
        {label}
      </div>
      <div className={`text-3xl font-bold font-mono ${color} leading-none mt-1`}>{value ?? '—'}</div>
      {sub && <div className="text-xs text-tg-muted mt-1">{sub}</div>}
    </div>
  );
}
