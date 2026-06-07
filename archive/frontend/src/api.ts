// Thin typed wrapper around the FastAPI backend.
// In dev, requests go to /api/* which Vite proxies to http://127.0.0.1:8000.

const BASE = "/api";

export type AgentColor = "yellow" | "cyan" | "green" | "white";

export interface Metrics {
  total_time_seconds: number;
  api_calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  model: string;
}

export interface AgentMessage {
  agent: string;
  color: AgentColor;
  content: string;
  timestamp: string;
  steps?: string[];
  needs_code?: boolean;
  code?: string;
  execution?: { success: boolean; stdout: string; stderr: string; returncode: number };
  file?: FileInfo;
  metrics?: Metrics; // attached to the last message of a response
}

export interface UserMessage {
  agent: "You";
  color: "white";
  content: string;
  timestamp: string;
  isUser: true;
}

export type ChatMessage = AgentMessage | UserMessage;

export interface Conversation {
  id: string;
  timestamp: string;
  model: string;
  user_message: string;
  preview: string;
  messages: AgentMessage[];
}

export interface FileInfo {
  name: string;
  size: number;
  size_human: string;
  modified: string;
  extension: string;
}

export interface AgentStatusItem {
  name: string;
  color: AgentColor;
  status: "idle" | "thinking" | "active" | "offline" | "error";
  last_activity: string | null;
}

export interface StatusResponse {
  backend_online: boolean;
  agents: AgentStatusItem[];
  memory_count: number;
  files_count: number;
}

export interface DashboardData {
  conversations: number;
  problems_solved_today: number;
  active_agents: number;
  total_agents: number;
  total_api_calls: number;
  total_tokens: number;
  system_status: string;
  activity: { date: string; count: number }[];
  recent_activity: { type: string; text: string; time: string }[];
  users: { count: number; implemented: boolean; label: string };
}

export interface SystemStats {
  cpu_percent: number | null;
  memory_percent: number | null;
  disk_percent: number | null;
  ollama_online: boolean;
}

export interface AdminUser {
  id: number;
  name: string;
  email: string;
  last_active: string;
  blocked: boolean;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    // Prefer the FastAPI "detail" message when available.
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) message = body.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

export const api = {
  chat: (message: string, model: string) =>
    request<{ conversation_id: string; route: "chat" | "task" | "code" | "learn"; messages: AgentMessage[]; model: string; metrics: Metrics }>(
      "/chat",
      {
        method: "POST",
        body: JSON.stringify({ message, model }),
      }
    ),

  history: () => request<{ count: number; conversations: Conversation[] }>("/history"),

  conversation: (id: string) =>
    request<{ conversation: Conversation | null }>(`/history/${id}`),

  files: () => request<{ count: number; files: FileInfo[] }>("/files"),

  status: () => request<StatusResponse>("/agents/status"),

  getSettings: () => request<{ settings: Record<string, string> }>("/settings"),

  getKeys: () =>
    request<{
      anthropic_api_key_set: boolean;
      anthropic_api_key_masked: string;
      groq_api_set: boolean;
      groq_api_masked: string;
      ollama_model: string;
    }>("/admin/keys"),

  saveKeys: (k: { anthropic_api_key?: string; groq_api?: string; ollama_model?: string }) =>
    request<{ ok: boolean; updated: string[] }>("/admin/keys", {
      method: "POST",
      body: JSON.stringify(k),
    }),

  updateSettings: (s: { model?: string; theme?: string }) =>
    request<{ settings: Record<string, string> }>("/settings", {
      method: "POST",
      body: JSON.stringify(s),
    }),

  clearMemory: () => request<{ ok: boolean; memory_count: number }>("/memory/clear", { method: "POST" }),

  dashboard: () => request<DashboardData>("/admin/dashboard"),

  system: () => request<SystemStats>("/admin/system"),

  adminAgents: () => request<{ agents: AgentStatusItem[] }>("/admin/agents"),

  controlAgent: (key: string, action: "start" | "stop") =>
    request<{ ok: boolean; agent: AgentStatusItem }>(`/admin/agents/${key}/${action}`, {
      method: "POST",
    }),

  adminUsers: () => request<{ users: AdminUser[] }>("/admin/users"),

  blockUser: (id: number, blocked: boolean) =>
    request<{ ok: boolean; user: AdminUser }>(`/admin/users/${id}`, {
      method: "POST",
      body: JSON.stringify({ blocked }),
    }),
};
