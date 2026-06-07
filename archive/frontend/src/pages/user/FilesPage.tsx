import { useEffect, useState } from "react";
import { api } from "../../api";
import type { FileInfo } from "../../api";
import { FileCode2, Download, Eye, X } from "lucide-react";

export default function FilesPage() {
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<{ name: string; content: string } | null>(null);

  const load = () =>
    api
      .files()
      .then((r) => setFiles(r.files))
      .catch(() => setFiles([]))
      .finally(() => setLoading(false));

  useEffect(() => {
    load();
  }, []);

  const openPreview = async (name: string) => {
    const text = await downloadText(name);
    setPreview({ name, content: text });
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="border-b border-white/10 px-6 py-3">
        <h1 className="text-sm font-semibold text-white">Files</h1>
        <p className="text-[11px] text-gray-500">{files.length} files created by agents</p>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <p className="text-gray-500">Loading…</p>
        ) : files.length === 0 ? (
          <p className="text-gray-500">No files yet. Ask the Coding agent to build something!</p>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {files.map((f) => (
              <div key={f.name} className="rounded-xl border border-white/5 bg-card p-4 transition hover:border-accent/40">
                <div className="mb-3 flex items-center gap-2">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent/10 text-accent">
                    <FileCode2 size={18} />
                  </div>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-gray-100" title={f.name}>
                      {f.name}
                    </p>
                    <p className="text-[11px] text-gray-500">
                      {f.size_human} · {new Date(f.modified).toLocaleDateString()}
                    </p>
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => openPreview(f.name)}
                    className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-white/10 py-1.5 text-xs text-gray-300 hover:bg-white/5"
                  >
                    <Eye size={14} /> Preview
                  </button>
                  <a
                    href={`/api/files/${encodeURIComponent(f.name)}/download`}
                    className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-accent/90 py-1.5 text-xs font-medium text-black hover:bg-accent"
                  >
                    <Download size={14} /> Download
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {preview && (
        <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/60 p-4" onClick={() => setPreview(null)}>
          <div
            className="flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-white/10 bg-base"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
              <h2 className="truncate text-sm font-semibold text-white">{preview.name}</h2>
              <button onClick={() => setPreview(null)} className="text-gray-400 hover:text-white">
                <X size={18} />
              </button>
            </div>
            <pre className="overflow-auto p-4 text-xs text-gray-200">
              <code>{preview.content}</code>
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

async function downloadText(name: string): Promise<string> {
  try {
    const res = await fetch(`/api/files/${encodeURIComponent(name)}/download`);
    if (!res.ok) return "Could not load file.";
    return await res.text();
  } catch {
    return "Could not load file.";
  }
}
