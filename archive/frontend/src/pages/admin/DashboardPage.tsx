import { useEffect, useState } from "react";
import type { JSX } from "react";
import { api } from "../../api";
import type { DashboardData, SystemStats, AgentStatusItem } from "../../api";
import {
  MessageSquare,
  CheckCircle2,
  Cpu,
  Activity,
  Hash,
  Coins,
  Users,
  Brain,
  Server,
  HardDrive,
  MemoryStick,
  FileCode2,
  AlertTriangle,
} from "lucide-react";
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/* All data on this page is REAL, fetched from the backend:
   /admin/dashboard, /admin/system, /admin/agents.
   The only honest placeholder is "Users" — accounts are not implemented yet. */

const fmt = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString());

const STATUS_DOT: Record<string, string> = {
  active: "bg-green-500",
  thinking: "bg-yellow-400 animate-pulse",
  idle: "bg-gray-500",
  offline: "bg-red-500",
  error: "bg-red-600",
};

function StatCard({ icon, label, value, accent }: { icon: JSX.Element; label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-xl border border-white/5 bg-card p-5 transition hover:border-accent/30">
      <div className={`mb-3 flex h-10 w-10 items-center justify-center rounded-lg ${accent ? "bg-accent/15 text-accent" : "bg-white/5 text-gray-300"}`}>
        {icon}
      </div>
      <div className="text-2xl font-bold text-white">{value}</div>
      <div className="mt-0.5 text-xs text-gray-500">{label}</div>
    </div>
  );
}

function Panel({ title, icon, children }: { title: string; icon: JSX.Element; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-white/5 bg-card p-5">
      <div className="mb-4 flex items-center gap-2">
        <span className="text-accent">{icon}</span>
        <h2 className="text-sm font-semibold text-white">{title}</h2>
      </div>
      {children}
    </div>
  );
}

function ResourceBar({ icon, label, value }: { icon: JSX.Element; label: string; value: number | null }) {
  const v = value ?? 0;
  const color = value == null ? "bg-gray-600" : v > 85 ? "bg-red-500" : v > 65 ? "bg-yellow-400" : "bg-accent";
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between text-xs">
        <span className="flex items-center gap-1.5 text-gray-400">{icon}{label}</span>
        <span className="text-gray-300">{value == null ? "—" : `${value}%`}</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-white/5">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${v}%` }} />
      </div>
    </div>
  );
}

function timeAgo(iso: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [system, setSystem] = useState<SystemStats | null>(null);
  const [agents, setAgents] = useState<AgentStatusItem[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    const load = () => {
      api.dashboard().then(setData).catch(() => setError(true));
      api.system().then(setSystem).catch(() => setSystem(null));
      api.adminAgents().then((r) => setAgents(r.agents)).catch(() => setAgents([]));
    };
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const online = data?.system_status === "online";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="border-b border-white/10 px-6 py-3">
        <h1 className="text-sm font-semibold text-white">Admin Dashboard</h1>
        <p className="text-[11px] text-gray-500">Live system data</p>
      </header>

      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-400">
            <AlertTriangle size={16} /> Could not reach the backend on port 8000.
          </div>
        )}

        {/* Stat cards — all REAL */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
          <StatCard icon={<MessageSquare size={18} />} label="Conversations" value={fmt(data?.conversations)} />
          <StatCard icon={<CheckCircle2 size={18} />} label="Solved Today" value={fmt(data?.problems_solved_today)} accent />
          <StatCard icon={<Cpu size={18} />} label="Active Agents" value={data ? `${data.active_agents} / ${data.total_agents}` : "—"} />
          <StatCard icon={<Hash size={18} />} label="API Calls (session)" value={fmt(data?.total_api_calls)} />
          <StatCard icon={<Coins size={18} />} label="Tokens (session)" value={fmt(data?.total_tokens)} />
          <StatCard icon={<Activity size={18} />} label="System" value={data ? (online ? "Online" : "Degraded") : "—"} accent={online} />
        </div>

        {/* Honest users banner */}
        <div className="mt-4 flex items-center gap-2 rounded-xl border border-yellow-500/20 bg-yellow-500/5 px-4 py-2.5 text-xs text-gray-400">
          <Users size={15} className="text-yellow-400/80" />
          <span>
            <span className="font-semibold text-gray-200">Users: {data?.users.label ?? "1 (local)"}</span> — multi-user
            accounts are not implemented yet (single local user).
          </span>
        </div>

        {/* Chart (real, 7 days) + Agent status (real) */}
        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <Panel title="Conversations — last 7 days" icon={<Activity size={18} />}>
              <div className="h-56 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data?.activity ?? []} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                    <XAxis dataKey="date" tickFormatter={(d) => String(d).slice(5)} stroke="#6b7280" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} />
                    <YAxis stroke="#6b7280" tick={{ fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
                    <Tooltip
                      cursor={{ fill: "rgba(255,255,255,0.04)" }}
                      contentStyle={{ background: "#1a1a1a", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, color: "#e5e5e5", fontSize: 12 }}
                    />
                    <Bar dataKey="count" radius={[6, 6, 0, 0]} maxBarSize={42}>
                      {(data?.activity ?? []).map((_, i) => (
                        <Cell key={i} fill="#00ff88" fillOpacity={0.85} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Panel>
          </div>

          <Panel title="Agent Status" icon={<Cpu size={18} />}>
            <div className="space-y-2">
              {agents.length === 0 && <p className="text-xs text-gray-600">No data.</p>}
              {agents.map((a) => (
                <div key={a.name} className="flex items-center justify-between rounded-lg border border-white/5 bg-base/40 px-3 py-2">
                  <span className="flex items-center gap-2 text-sm text-gray-200">
                    <span className={`h-2.5 w-2.5 rounded-full ${STATUS_DOT[a.status] ?? "bg-gray-500"}`} />
                    {a.name}
                  </span>
                  <span className="text-[11px] text-gray-500">{a.status}</span>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        {/* Recent activity (real) + System resources (real) */}
        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel title="Recent Activity" icon={<Activity size={18} />}>
            {data && data.recent_activity.length === 0 && (
              <p className="text-xs text-gray-600">No activity yet — start a conversation.</p>
            )}
            <ul className="space-y-1">
              {(data?.recent_activity ?? []).map((e, i) => (
                <li key={i} className="flex items-center gap-3 rounded-lg px-2 py-2 transition hover:bg-white/5">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent">
                    {e.type === "file" ? <FileCode2 size={15} /> : <MessageSquare size={15} />}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm text-gray-300">{e.text}</span>
                  <span className="shrink-0 text-[11px] text-gray-600">{timeAgo(e.time)}</span>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="System Resources" icon={<Server size={18} />}>
            <div className="space-y-4">
              <ResourceBar icon={<Cpu size={14} />} label="CPU" value={system?.cpu_percent ?? null} />
              <ResourceBar icon={<MemoryStick size={14} />} label="Memory" value={system?.memory_percent ?? null} />
              <ResourceBar icon={<HardDrive size={14} />} label="Disk" value={system?.disk_percent ?? null} />
              <div className="mt-1 flex items-center justify-between border-t border-white/5 pt-3 text-sm">
                <span className="flex items-center gap-2 text-gray-400">
                  <Brain size={15} className="text-accent" /> Ollama
                </span>
                <span className="flex items-center gap-1.5 text-xs">
                  <span className={`h-2.5 w-2.5 rounded-full ${system?.ollama_online ? "bg-green-500" : "bg-red-500"}`} />
                  <span className={system?.ollama_online ? "text-green-400" : "text-red-400"}>
                    {system?.ollama_online ? "Online" : "Offline"}
                  </span>
                </span>
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
