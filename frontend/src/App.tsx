import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { ProjectLayout } from "./components/layout/ProjectLayout";
import { PageLoader } from "./components/ui";
import { useAuth } from "./lib/auth";
import Dashboard from "./pages/Dashboard";
import Discovery from "./pages/Discovery";
import Library from "./pages/Library";
import ProjectContextPanel from "./pages/ProjectContextPanel";
import ProjectSettings from "./pages/ProjectSettings";
import Settings from "./pages/Settings";

// Local single-user gate: wait for the auto-created local user to load, then always
// render (there is no login page to bounce to — the backend never 401s a request).
function AppGate({ children }: { children: React.ReactNode }) {
  const { loading } = useAuth();
  if (loading) return <PageLoader />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/app" replace />} />

      <Route
        path="/app"
        element={
          <AppGate>
            <AppShell />
          </AppGate>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="library" element={<Library />} />
        <Route path="settings" element={<Settings />} />
        <Route path="projects/:id" element={<ProjectLayout />}>
          <Route index element={<Navigate to="discovery" replace />} />
          <Route path="discovery" element={<Discovery />} />
          <Route path="context" element={<ProjectContextPanel />} />
          <Route path="settings" element={<ProjectSettings />} />
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/app" replace />} />
    </Routes>
  );
}
