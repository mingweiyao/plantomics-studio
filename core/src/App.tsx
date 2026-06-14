import { Routes, Route, Navigate } from "react-router-dom";
import { Welcome } from "./routes/Welcome";
import { ProjectList } from "./routes/ProjectList";
import { ProjectDetail } from "./routes/ProjectDetail";
import { NewProject } from "./routes/NewProject";
import { Modules } from "./routes/Modules";
import { ModulePage } from "./routes/ModulePage";
import { Settings } from "./routes/Settings";
import { Upstream } from "./routes/Upstream";
import { AnalysisHome } from "./routes/AnalysisHome";
import { AppShell } from "./components/AppShell";
import { ProjectLayout } from "./components/ProjectLayout";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/welcome" replace />} />
      <Route path="/welcome" element={<Welcome />} />
      <Route element={<AppShell />}>
        <Route path="/projects" element={<ProjectList />} />
        <Route path="/projects/new" element={<NewProject />} />
        <Route path="/projects/:id" element={<ProjectLayout />}>
          <Route index element={<ProjectDetail />} />
          <Route path="m/omics-rnaseq-bulk/upstream" element={<Upstream />} />
          <Route path="m/omics-analysis/downstream" element={<AnalysisHome />} />
          <Route path="m/:moduleId/*" element={<ModulePage />} />
        </Route>
        <Route path="/modules" element={<Modules />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  );
}
