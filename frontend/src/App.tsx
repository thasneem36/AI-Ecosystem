import { Routes, Route, Navigate } from "react-router-dom";
import Sidebar from "./components/Sidebar";

import ChatPage from "./pages/user/ChatPage";
import HistoryPage from "./pages/user/HistoryPage";
import FilesPage from "./pages/user/FilesPage";

import DashboardPage from "./pages/admin/DashboardPage";
import AgentControlPage from "./pages/admin/AgentControlPage";
import UserManagementPage from "./pages/admin/UserManagementPage";
import SystemSettingsPage from "./pages/admin/SystemSettingsPage";

export default function App() {
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-base text-gray-200">
      <Sidebar />
      <main className="flex flex-1 flex-col overflow-hidden">
        <Routes>
          {/* AI workspace */}
          <Route path="/" element={<ChatPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/files" element={<FilesPage />} />
          {/* Admin */}
          <Route path="/admin" element={<DashboardPage />} />
          <Route path="/admin/agents" element={<AgentControlPage />} />
          <Route path="/admin/users" element={<UserManagementPage />} />
          <Route path="/admin/settings" element={<SystemSettingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
