import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Bot, User, Lightbulb, Cog, Code2, GraduationCap } from "lucide-react";
import type { ChatMessage, Metrics } from "../api";

const AGENT_STYLES: Record<string, { ring: string; text: string; bg: string; icon: JSX.Element }> = {
  Planner: { ring: "border-agent-planner/40", text: "text-agent-planner", bg: "bg-agent-planner/10", icon: <Lightbulb size={16} /> },
  Executor: { ring: "border-agent-executor/40", text: "text-agent-executor", bg: "bg-agent-executor/10", icon: <Cog size={16} /> },
  Coding: { ring: "border-agent-coding/40", text: "text-agent-coding", bg: "bg-agent-coding/10", icon: <Code2 size={16} /> },
  Tutor: { ring: "border-purple-400/40", text: "text-purple-300", bg: "bg-purple-400/10", icon: <GraduationCap size={16} /> },
};

function isUser(m: ChatMessage): boolean {
  return (m as any).isUser === true || m.agent === "You";
}

export default function Message({ message }: { message: ChatMessage }) {
  if (isUser(message)) {
    return (
      <div className="flex justify-end animate-fade-in">
        <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-white text-black px-4 py-2.5 shadow">
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        </div>
        <div className="ml-2 mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-white/10 text-white">
          <User size={16} />
        </div>
      </div>
    );
  }

  const style = AGENT_STYLES[message.agent] ?? {
    ring: "border-white/10",
    text: "text-gray-300",
    bg: "bg-white/5",
    icon: <Bot size={16} />,
  };

  const metrics = (message as any).metrics as Metrics | undefined;

  return (
    <div className="flex justify-start animate-fade-in">
      <div className={`mr-2 mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${style.bg} ${style.text}`}>
        {style.icon}
      </div>
      <div className="flex max-w-[80%] flex-col">
        <div className={`rounded-2xl rounded-tl-sm border ${style.ring} ${style.bg} px-4 py-3`}>
          <div className={`mb-1 text-xs font-semibold uppercase tracking-wide ${style.text}`}>
            {message.agent} Agent
          </div>
          <div className="prose prose-invert prose-sm max-w-none text-gray-200">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                code({ inline, className, children, ...props }: any) {
                  const match = /language-(\w+)/.exec(className || "");
                  if (!inline && match) {
                    return (
                      <SyntaxHighlighter style={oneDark} language={match[1]} PreTag="div" customStyle={{ borderRadius: 8, fontSize: 13 }}>
                        {String(children).replace(/\n$/, "")}
                      </SyntaxHighlighter>
                    );
                  }
                  return (
                    <code className="rounded bg-black/40 px-1 py-0.5 text-[0.85em] text-accent" {...props}>
                      {children}
                    </code>
                  );
                },
              }}
            >
              {message.content}
            </ReactMarkdown>
          </div>
        </div>
        {metrics && <MetricsLine m={metrics} />}
      </div>
    </div>
  );
}

function MetricsLine({ m }: { m: Metrics }) {
  const fmt = (n: number) => n.toLocaleString();
  const noTokens = m.input_tokens == null && m.output_tokens == null;
  const tokenPart = noTokens
    ? "🔤 tokens: n/a"
    : `🔤 ${fmt(m.total_tokens ?? 0)} tokens (${fmt(m.input_tokens ?? 0)} in / ${fmt(m.output_tokens ?? 0)} out)`;
  return (
    <div className="mt-1 px-1 text-[10px] text-gray-600">
      ⏱ {m.total_time_seconds}s · 🔁 {m.api_calls} {m.api_calls === 1 ? "call" : "calls"} · {tokenPart} · {m.model}
    </div>
  );
}
