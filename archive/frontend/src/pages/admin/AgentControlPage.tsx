import { useEffect, useState } from "react";
import { api } from "../../api";
import type { AgentStatusItem } from "../../api";
import { Play, Square, Cpu } from "lucide-react";

const KEY_MAP: Record<string, string> = {
  Router: "router",
  Planner: "planner",
  Executor: "executor",
  Coding: "coding",
  "Web Search": "search",
};
// The Router always runs and can't be toggled.
const LOCKED = new Set(["Router"]);

const DOT: Record<string, string> = {
  active: "bg-green-500",
  thinking: "bg-yellow-400 animate-pulse",
  idle: "bg-gray-500",
  offline: "bg-red-500",
  error: "bg-red-600",
};

export default function AgentControlPage() {
  const [agents, setAgents] = useState<AgentStatusItem[]>([]);

  const load = () => api.adminAgents().then((r) => setAgents(r.agents)).catch(() => setAgents([]));

  useEffect(() => {
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, []);

  const control = async (name: string, action: "start" | "stop") => {
    const key = KEY_MAP[name];
    if (!key) return;
    await api.controlAgent(key, action).catch(() => {});
    load();
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="border-b border-white/10 px-6 py-3">
        <h1 className="text-sm font-semibold text-white">Agent Control</h1>
        <p className="text-[11px] text-gray-500">Start or stop individual agents</p>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-2xl space-y-3">
          {agents.map((a) => {
            const online = a.status === "active" || a.status === "thinking";
            return (
              <div key={a.name} className="flex items-center justify-between rounded-xl border border-white/5 bg-card p-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-white/5 text-gray-300">
                    <Cpu size={18} />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className={`h-2.5 w-2.5 rounded-full ${DOT[a.status] ?? "bg-gray-500"}`} />
                      <span className="text-sm font-medium text-white">{a.name} Agent</span>
                    </div>
                    <div className="text-[11px] text-gray-500">
                      {a.last_activity ? `Last active: ${new Date(a.last_activity).toLocaleString()}` : "No activity yet"}
                    </div>
                  </div>
                </div>
                <div className="flex gap-2">
                  {LOCKED.has(a.name) ? (
                    <span className="rounded-lg bg-white/5 px-3 py-1.5 text-xs text-gray-500">Always on</span>
                  ) : (
                    <>
                      <button
                        onClick={() => control(a.name, "start")}
                        disabled={online}
                        className="flex items-center gap-1.5 rounded-lg bg-green-500/15 px-3 py-1.5 text-xs text-green-400 transition hover:bg-green-500/25 disabled:opacity-30"
                      >
                        <Play size={14} /> Start
                      </button>
                      <button
                        onClick={() => control(a.name, "stop")}
                        disabled={a.status === "offline"}
                        className="flex items-center gap-1.5 rounded-lg bg-red-500/15 px-3 py-1.5 text-xs text-red-400 transition hover:bg-red-500/25 disabled:opacity-30"
                      >
                        <Square size={14} /> Stop
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
          {agents.length === 0 && <p className="text-gray-500">No agents found.</p>}
        </div>
      </div>
    </div>
  );
}
