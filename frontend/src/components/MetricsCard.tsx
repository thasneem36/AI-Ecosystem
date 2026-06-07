import { Activity } from "lucide-react";

/** Running session totals + the most recent message's metrics. */
export interface SessionTotals {
  calls: number;
  tokens: number;
}
export interface LastMetrics {
  calls: number | null;
  tokens: number | null;
  time: number | null;
}

const fmt = (n: number | null | undefined): string =>
  n === null || n === undefined ? "—" : n.toLocaleString();

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2 leading-snug">
      <span className="text-gray-500">{label}</span>
      <span className="font-semibold text-gray-200">{value}</span>
    </div>
  );
}

export default function MetricsCard({
  session,
  last,
}: {
  session: SessionTotals;
  last: LastMetrics;
}) {
  return (
    <div className="w-28 rounded-xl border border-white/10 bg-card/95 p-2.5 text-[10px] shadow-lg backdrop-blur">
      <div className="mb-1 flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wider text-accent">
        <Activity size={10} /> Metrics
      </div>

      <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-wider text-gray-500">
        Session
      </div>
      <Row label="calls" value={fmt(session.calls)} />
      <Row label="tokens" value={fmt(session.tokens)} />

      <div className="my-1.5 border-t border-white/10" />

      <div className="mb-0.5 text-[9px] font-semibold uppercase tracking-wider text-gray-500">
        Last chat
      </div>
      <Row label="calls" value={fmt(last.calls)} />
      <Row label="tokens" value={fmt(last.tokens)} />
      <Row label="time" value={last.time === null || last.time === undefined ? "—" : `${last.time}s`} />
    </div>
  );
}
