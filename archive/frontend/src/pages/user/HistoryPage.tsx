import { useEffect, useState } from "react";
import { api } from "../../api";
import type { Conversation, ChatMessage } from "../../api";
import { MessageSquare, Clock, X } from "lucide-react";
import Message from "../../components/Message";

export default function HistoryPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .history()
      .then((r) => setConversations(r.conversations))
      .catch(() => setConversations([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="border-b border-white/10 px-6 py-3">
        <h1 className="text-sm font-semibold text-white">History</h1>
        <p className="text-[11px] text-gray-500">{conversations.length} past conversations</p>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <p className="text-gray-500">Loading…</p>
        ) : conversations.length === 0 ? (
          <p className="text-gray-500">No conversations yet.</p>
        ) : (
          <div className="mx-auto max-w-3xl space-y-2">
            {conversations.map((c) => (
              <button
                key={c.id}
                onClick={() => setSelected(c)}
                className="flex w-full items-start gap-3 rounded-xl border border-white/5 bg-card p-4 text-left transition hover:border-accent/40"
              >
                <MessageSquare size={18} className="mt-0.5 shrink-0 text-accent" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-gray-200">{c.preview || c.user_message}</p>
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-gray-500">
                    <Clock size={12} />
                    {new Date(c.timestamp).toLocaleString()}
                    <span className="rounded bg-white/5 px-1.5 py-0.5">{c.model}</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {selected && (
        <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/60 p-4" onClick={() => setSelected(null)}>
          <div
            className="flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/10 bg-base"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
              <h2 className="truncate text-sm font-semibold text-white">{selected.preview}</h2>
              <button onClick={() => setSelected(null)} className="text-gray-400 hover:text-white">
                <X size={18} />
              </button>
            </div>
            <div className="flex flex-col gap-4 overflow-y-auto p-4">
              <Message
                message={{
                  agent: "You",
                  color: "white",
                  content: selected.user_message,
                  timestamp: selected.timestamp,
                  isUser: true,
                } as ChatMessage}
              />
              {selected.messages.map((m, i) => (
                <Message key={i} message={m} />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
