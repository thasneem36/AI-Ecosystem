import { NavLink } from "react-router-dom";
import {
  MessageSquare,
  History,
  FolderOpen,
  LayoutDashboard,
  Cpu,
  Users,
  SlidersHorizontal,
  Sparkles,
} from "lucide-react";

const aiLinks = [
  { to: "/", label: "Chat", icon: MessageSquare, end: true },
  { to: "/history", label: "History", icon: History },
  { to: "/files", label: "Files", icon: FolderOpen },
];

const adminLinks = [
  { to: "/admin", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/admin/agents", label: "Agent Control", icon: Cpu },
  { to: "/admin/users", label: "Users", icon: Users },
  { to: "/admin/settings", label: "System Settings", icon: SlidersHorizontal },
];

function linkClass({ isActive }: { isActive: boolean }) {
  return [
    "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition",
    isActive ? "bg-accent/10 text-accent" : "text-gray-400 hover:bg-white/5 hover:text-gray-200",
  ].join(" ");
}

export default function Sidebar() {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-white/10 bg-sidebar p-4">
      <div className="mb-6 flex items-center gap-2 px-1">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent text-black">
          <Sparkles size={18} />
        </div>
        <div>
          <div className="text-sm font-bold leading-tight text-white">AI Ecosystem</div>
          <div className="text-[11px] text-gray-500">admin console</div>
        </div>
      </div>

      <div className="mb-2 px-3 text-[11px] font-semibold uppercase tracking-wider text-gray-600">
        Workspace
      </div>
      <nav className="space-y-1">
        {aiLinks.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end} className={linkClass}>
            <l.icon size={18} />
            {l.label}
          </NavLink>
        ))}
      </nav>

      <div className="mt-6 mb-2 px-3 text-[11px] font-semibold uppercase tracking-wider text-gray-600">
        Admin
      </div>
      <nav className="space-y-1">
        {adminLinks.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end} className={linkClass}>
            <l.icon size={18} />
            {l.label}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto px-2 pt-4 text-[11px] text-gray-600">v1.0.0 · local-first</div>
    </aside>
  );
}
