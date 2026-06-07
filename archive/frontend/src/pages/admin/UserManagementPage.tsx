import { useEffect, useState } from "react";
import { api } from "../../api";
import type { AdminUser } from "../../api";
import { Ban, CheckCircle2 } from "lucide-react";

export default function UserManagementPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);

  const load = () => api.adminUsers().then((r) => setUsers(r.users)).catch(() => setUsers([]));

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const toggleBlock = async (u: AdminUser) => {
    await api.blockUser(u.id, !u.blocked).catch(() => {});
    load();
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="border-b border-white/10 px-6 py-3">
        <h1 className="text-sm font-semibold text-white">User Management</h1>
        <p className="text-[11px] text-gray-500">Demo — accounts not implemented yet</p>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto mb-4 max-w-4xl rounded-xl border border-yellow-500/20 bg-yellow-500/5 px-4 py-2.5 text-xs text-gray-400">
          ⚠️ There is no account system or database yet. This shows only the single local user.
          The block/unblock action is a non-persistent demo.
        </div>
        <div className="mx-auto max-w-4xl overflow-hidden rounded-xl border border-white/5 bg-card">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-white/10 text-xs uppercase tracking-wider text-gray-500">
              <tr>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Email</th>
                <th className="px-4 py-3">Last active</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-white/5 last:border-0">
                  <td className="px-4 py-3 text-gray-100">{u.name}</td>
                  <td className="px-4 py-3 text-gray-400">{u.email}</td>
                  <td className="px-4 py-3 text-gray-500">{new Date(u.last_active).toLocaleString()}</td>
                  <td className="px-4 py-3">
                    {u.blocked ? (
                      <span className="rounded bg-red-500/15 px-2 py-0.5 text-xs text-red-400">Blocked</span>
                    ) : (
                      <span className="rounded bg-green-500/15 px-2 py-0.5 text-xs text-green-400">Active</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => toggleBlock(u)}
                      className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition ${
                        u.blocked
                          ? "bg-green-500/15 text-green-400 hover:bg-green-500/25"
                          : "bg-red-500/15 text-red-400 hover:bg-red-500/25"
                      }`}
                    >
                      {u.blocked ? <CheckCircle2 size={14} /> : <Ban size={14} />}
                      {u.blocked ? "Unblock" : "Block"}
                    </button>
                  </td>
                </tr>
              ))}
              {users.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-6 text-center text-gray-500">
                    No users found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
