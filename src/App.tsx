import { Routes, Route } from "react-router-dom";
import { useSidecar } from "./hooks/useSidecar";
import { useAuth } from "./hooks/useAuth";
import AuthGuard from "./components/AuthGuard";
import Sidebar from "./components/Sidebar";
import StatusBar from "./components/StatusBar";
import Dashboard from "./pages/Dashboard";
import Positions from "./pages/Positions";
import History from "./pages/History";
import Settings from "./pages/Settings";
import Logs from "./pages/Logs";
import Tutorial from "./pages/Tutorial";
import Profile from "./pages/Profile";
import Admin from "./pages/Admin";

export default function App() {
  useSidecar();
  useAuth();

  return (
    <AuthGuard>
      <div className="flex h-screen overflow-hidden" style={{ background: "var(--color-bg-base)" }}>
        <Sidebar />
        <div className="flex-1 flex flex-col min-w-0">
          <main className="flex-1 overflow-y-auto" style={{ padding: "28px 32px" }}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/positions" element={<Positions />} />
              <Route path="/history" element={<History />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/logs" element={<Logs />} />
              <Route path="/tutorial" element={<Tutorial />} />
              <Route path="/profile" element={<Profile />} />
              <Route path="/admin" element={<Admin />} />
            </Routes>
          </main>
          <StatusBar />
        </div>
      </div>
    </AuthGuard>
  );
}
