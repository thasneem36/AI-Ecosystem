import type { AgentStatusItem } from "../api";

const DOT: Record<string, string> = {
  active: "bg-green-500",
  thinking: "bg-yellow-400 animate-pulse",
  idle: "bg-gray-500",
  offline: "bg-red-500",
  error: "bg-red-600",
};

const LABEL: Record<string, string> = {
  active: "Active",
  thinking: "Thinking",
  idle: "Idle",
  offline: "Offline",
  error: "Error",
};

export default function AgentStatus({ agents }: { agents: AgentStatusItem[] }) {
  return (
    <div className="space-y-2">
      {agents.map((a) => (
        <div
          key={a.name}
          className="flex items-center justify-between rounded-lg border border-white/5 bg-card px-3 py-2"
        >
          <div className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${DOT[a.status] ?? "bg-gray-500"}`} />
            <span className="text-sm text-gray-200">{a.name}</span>
          </div>
          <span className="text-xs text-gray-500">{LABEL[a.status] ?? a.status}</span>
        </div>
      ))}
    </div>
  );
}
