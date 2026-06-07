import { useEffect, useState } from "react";
import ChatBox from "../../components/ChatBox";
import InputBar from "../../components/InputBar";
import AgentStatus from "../../components/AgentStatus";
import MetricsCard from "../../components/MetricsCard";
import type { LastMetrics, SessionTotals } from "../../components/MetricsCard";
import { api } from "../../api";
import type { ChatMessage, StatusResponse } from "../../api";
import { Brain, FileCode2, Wifi, WifiOff } from "lucide-react";

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [thinking, setThinking] = useState(false);
  const [model, setModel] = useState("ollama");
  const [status, setStatus] = useState<StatusResponse | null>(null);
  // Metrics: running session totals + the most recent message's metrics.
  const [session, setSession] = useState<SessionTotals>({ calls: 0, tokens: 0 });
  const [last, setLast] = useState<LastMetrics>({ calls: null, tokens: null, time: null });

  const refreshStatus = async () => {
    try {
      setStatus(await api.status());
    } catch {
      setStatus(null);
    }
  };

  useEffect(() => {
    refreshStatus();
    const id = setInterval(refreshStatus, 5000);
    // Seed the model selector from the backend's configured default
    // (config/settings.py → DEFAULT_BACKEND), so the central setting wins.
    api
      .getSettings()
      .then((r) => {
        if (r.settings.model) setModel(r.settings.model);
      })
      .catch(() => {});
    return () => clearInterval(id);
  }, []);

  const handleSend = async (text: string) => {
    setMessages((m) => [
      ...m,
      { agent: "You", color: "white", content: text, timestamp: new Date().toISOString(), isUser: true },
    ]);
    setThinking(true);
    try {
      const res = await api.chat(text, model);
      // Attach the response metrics to the last bubble of this turn.
      const batch = res.messages.map((msg, i) =>
        i === res.messages.length - 1 ? { ...msg, metrics: res.metrics } : msg
      );
      setMessages((m) => [...m, ...batch]);

      // Update the floating metrics card: last-chat values + running session totals.
      const mx = res.metrics;
      setLast({
        calls: mx?.api_calls ?? null,
        tokens: mx?.total_tokens ?? null,
        time: mx?.total_time_seconds ?? null,
      });
      setSession((s) => ({
        calls: s.calls + (mx?.api_calls ?? 0),
        tokens: s.tokens + (mx?.total_tokens ?? 0),
      }));
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          agent: "Executor",
          color: "cyan",
          content: `⚠️ Could not reach the backend. Make sure it is running on port 8000.\n\n\`${String(e)}\``,
          timestamp: new Date().toISOString(),
        },
      ]);
    } finally {
      setThinking(false);
      refreshStatus();
    }
  };

  return (
    <div className="flex h-full overflow-hidden">
      {/* Left info panel */}
      <div className="hidden w-64 shrink-0 flex-col gap-5 overflow-y-auto border-r border-white/10 bg-sidebar/60 p-4 md:flex">
        <div>
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">Agents</h3>
            {status?.backend_online ? (
              <span className="flex items-center gap-1 text-[11px] text-green-500">
                <Wifi size={12} /> online
              </span>
            ) : (
              <span className="flex items-center gap-1 text-[11px] text-red-500">
                <WifiOff size={12} /> offline
              </span>
            )}
          </div>
          <AgentStatus agents={status?.agents ?? []} />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-lg border border-white/5 bg-card p-3">
            <Brain size={16} className="mb-1 text-accent" />
            <div className="text-lg font-bold text-white">{status?.memory_count ?? 0}</div>
            <div className="text-[11px] text-gray-500">memories</div>
          </div>
          <div className="rounded-lg border border-white/5 bg-card p-3">
            <FileCode2 size={16} className="mb-1 text-accent" />
            <div className="text-lg font-bold text-white">{status?.files_count ?? 0}</div>
            <div className="text-[11px] text-gray-500">files</div>
          </div>
        </div>

        <OutputFilesList />
      </div>

      {/* Chat column */}
      <div className="relative flex flex-1 flex-col overflow-hidden">
        <header className="border-b border-white/10 px-6 py-3">
          <h1 className="text-sm font-semibold text-white">Chat</h1>
          <p className="text-[11px] text-gray-500">Planner → Executor → Coding pipeline</p>
        </header>
        <ChatBox messages={messages} thinking={thinking} />

        {/* Floating metrics card — pinned bottom-left, stays put while messages scroll */}
        <div className="pointer-events-none absolute bottom-24 left-3 z-20">
          <MetricsCard session={session} last={last} />
        </div>

        <InputBar onSend={handleSend} disabled={thinking} model={model} onModelChange={setModel} />
      </div>
    </div>
  );
}

function OutputFilesList() {
  const [files, setFiles] = useState<string[]>([]);
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const res = await api.files();
        if (active) setFiles(res.files.map((f) => f.name));
      } catch {
        /* ignore */
      }
    };
    load();
    const id = setInterval(load, 6000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);

  return (
    <div>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-gray-500">Output files</h3>
      {files.length === 0 ? (
        <p className="text-[11px] text-gray-600">No files yet.</p>
      ) : (
        <ul className="space-y-1">
          {files.map((f) => (
            <li key={f} className="truncate rounded bg-card px-2 py-1 text-[11px] text-gray-300" title={f}>
              {f}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
