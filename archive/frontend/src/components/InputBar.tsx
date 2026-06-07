import { useState } from "react";
import { Send } from "lucide-react";

interface Props {
  onSend: (text: string) => void;
  disabled?: boolean;
  model: string;
  onModelChange: (model: string) => void;
}

export default function InputBar({ onSend, disabled, model, onModelChange }: Props) {
  const [text, setText] = useState("");

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  };

  return (
    <div className="border-t border-white/10 bg-base/80 p-3 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <select
          value={model}
          onChange={(e) => onModelChange(e.target.value)}
          className="h-11 rounded-xl border border-white/10 bg-card px-3 text-sm text-gray-300 outline-none focus:border-accent"
          title="Model"
        >
          <option value="ollama">Ollama</option>
          <option value="groq">Groq</option>
          <option value="claude">Claude API</option>
        </select>

        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={1}
          placeholder="Ask the agents anything…"
          className="max-h-40 flex-1 resize-none rounded-xl border border-white/10 bg-card px-4 py-3 text-sm text-gray-100 outline-none placeholder:text-gray-500 focus:border-accent"
        />

        <button
          onClick={submit}
          disabled={disabled}
          className="flex h-11 items-center gap-2 rounded-xl bg-accent px-4 font-semibold text-black transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <span>🚀</span>
          <Send size={16} />
        </button>
      </div>
    </div>
  );
}
