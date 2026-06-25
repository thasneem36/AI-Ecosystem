import { useEffect, useRef } from "react";
import Message from "./Message";
import type { ChatMessage } from "../api";

interface Props {
  messages: ChatMessage[];
  thinking?: boolean;
}

function TypingIndicator() {
  return (
    <div className="flex items-center gap-2 animate-fade-in">
      <div className="flex h-8 w-8 items-center justify-center rounded-full bg-white/5 text-accent">
        ●
      </div>
      <div className="flex items-center gap-1 rounded-2xl rounded-tl-sm border border-white/10 bg-white/5 px-4 py-3">
        <span className="h-2 w-2 rounded-full bg-accent animate-blink" style={{ animationDelay: "0ms" }} />
        <span className="h-2 w-2 rounded-full bg-accent animate-blink" style={{ animationDelay: "200ms" }} />
        <span className="h-2 w-2 rounded-full bg-accent animate-blink" style={{ animationDelay: "400ms" }} />
        <span className="ml-2 text-xs text-gray-400">agents are thinking…</span>
      </div>
    </div>
  );
}

export default function ChatBox({ messages, thinking }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, thinking]);

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-4">
        {messages.length === 0 && !thinking && (
          <div className="mt-20 text-center text-gray-500">
            <div className="mb-3 text-5xl">🤖</div>
            <h2 className="text-lg font-semibold text-gray-300">Welcome to Koottam</h2>
            <p className="mt-1 text-sm">
              Ask a question — the <span className="text-agent-planner">Planner</span>,{" "}
              <span className="text-agent-executor">Executor</span> and{" "}
              <span className="text-agent-coding">Coding</span> agents will collaborate to answer.
            </p>
          </div>
        )}
        {messages.map((m, i) => (
          <Message key={i} message={m} />
        ))}
        {thinking && <TypingIndicator />}
        <div ref={endRef} />
      </div>
    </div>
  );
}
