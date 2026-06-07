import { useEffect, useState } from "react";
import { api } from "../../api";
import { KeyRound, Cpu, Save, Check, ShieldCheck } from "lucide-react";

export default function SystemSettingsPage() {
  const [model, setModel] = useState("ollama");
  const [ollamaModel, setOllamaModel] = useState("qwen3.5");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [groqKey, setGroqKey] = useState("");
  const [anthropicSet, setAnthropicSet] = useState(false);
  const [groqSet, setGroqSet] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  const loadKeys = () =>
    api
      .getKeys()
      .then((k) => {
        setAnthropicSet(k.anthropic_api_key_set);
        setGroqSet(k.groq_api_set);
        if (k.ollama_model) setOllamaModel(k.ollama_model);
      })
      .catch(() => {});

  useEffect(() => {
    api.getSettings().then((r) => r.settings.model && setModel(r.settings.model)).catch(() => {});
    loadKeys();
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await api.updateSettings({ model });
      // Only send fields the admin actually typed (don't overwrite with blanks).
      const keys: { anthropic_api_key?: string; groq_api?: string; ollama_model?: string } = {};
      if (anthropicKey.trim()) keys.anthropic_api_key = anthropicKey.trim();
      if (groqKey.trim()) keys.groq_api = groqKey.trim();
      if (ollamaModel.trim()) keys.ollama_model = ollamaModel.trim();
      if (Object.keys(keys).length) await api.saveKeys(keys);

      setAnthropicKey("");
      setGroqKey("");
      await loadKeys();
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="border-b border-white/10 px-6 py-3">
        <h1 className="text-sm font-semibold text-white">System Settings</h1>
        <p className="text-[11px] text-gray-500">API keys and model configuration</p>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-xl space-y-6">
          <section className="rounded-xl border border-white/5 bg-card p-5">
            <div className="mb-4 flex items-center gap-2">
              <KeyRound size={18} className="text-accent" />
              <h2 className="text-sm font-semibold text-white">API Keys</h2>
            </div>
            <div className="space-y-4">
              <Field label="Ollama model" value={ollamaModel} onChange={setOllamaModel} placeholder="qwen3.5" />
              <Field
                label="Anthropic API key"
                value={anthropicKey}
                onChange={setAnthropicKey}
                placeholder={anthropicSet ? "•••••••• (configured — type to replace)" : "sk-ant-…"}
                type="password"
                configured={anthropicSet}
              />
              <Field
                label="Groq API key"
                value={groqKey}
                onChange={setGroqKey}
                placeholder={groqSet ? "•••••••• (configured — type to replace)" : "gsk_…"}
                type="password"
                configured={groqSet}
              />
            </div>
            <p className="mt-3 text-[11px] text-gray-500">
              Keys are saved securely to the backend <code className="text-accent">.env</code> and applied
              immediately — no restart needed. Existing keys are never shown in full.
            </p>
          </section>

          <section className="rounded-xl border border-white/5 bg-card p-5">
            <div className="mb-4 flex items-center gap-2">
              <Cpu size={18} className="text-accent" />
              <h2 className="text-sm font-semibold text-white">Default Model</h2>
            </div>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-base px-3 py-2.5 text-sm text-gray-200 outline-none focus:border-accent"
            >
              <option value="ollama">Ollama (local)</option>
              <option value="groq">Groq (hosted Llama)</option>
              <option value="claude">Claude API</option>
            </select>
          </section>

          <button
            onClick={save}
            disabled={saving}
            className="flex items-center gap-2 rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-black transition hover:brightness-110 disabled:opacity-50"
          >
            {saved ? <Check size={16} /> : <Save size={16} />}
            {saved ? "Saved" : saving ? "Saving…" : "Save settings"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  configured,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  configured?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-1.5 text-xs text-gray-400">
        {label}
        {configured && (
          <span className="inline-flex items-center gap-1 text-[10px] text-accent">
            <ShieldCheck size={11} /> configured
          </span>
        )}
      </span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-white/10 bg-base px-3 py-2.5 text-sm text-gray-200 outline-none focus:border-accent"
      />
    </label>
  );
}
