import { useEffect } from "react";
import { Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "@/components/ProtectedRoute";
import AcceptInvite from "@/routes/AcceptInvite";
import AdminInvites from "@/routes/AdminInvites";
import ForgotPassword from "@/routes/ForgotPassword";
import HmoStudio from "@/routes/HmoStudio";
import Login from "@/routes/Login";
import ProjectDetail from "@/routes/ProjectDetail";
import ProjectHistory from "@/routes/ProjectHistory";
import ProjectsList from "@/routes/ProjectsList";
import ResetPassword from "@/routes/ResetPassword";
import RunDetail from "@/routes/RunDetail";
import RunOverview from "@/routes/RunOverview";
import Settings from "@/routes/Settings";
import StageExtraction from "@/routes/StageExtraction";
import StageRdf from "@/routes/StageRdf";
import WikidataStudio from "@/routes/WikidataStudio";
import { useAuth } from "@/stores/auth";

export default function App() {
  const bootstrap = useAuth((s) => s.bootstrap);
  useEffect(() => { void bootstrap(); }, [bootstrap]);

  return (
    <Routes>
      {/* Public */}
      <Route path="/login"           element={<Login />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password"  element={<ResetPassword />} />
      <Route path="/accept-invite"   element={<AcceptInvite />} />

      {/* Authenticated */}
      <Route path="/"                            element={<ProtectedRoute><ProjectsList /></ProtectedRoute>} />
      <Route path="/projects/:projectId"         element={<ProtectedRoute><ProjectDetail /></ProtectedRoute>} />
      <Route path="/projects/:projectId/history" element={<ProtectedRoute><ProjectHistory /></ProtectedRoute>} />
      <Route path="/runs/:runId"                 element={<ProtectedRoute><RunDetail /></ProtectedRoute>} />
      <Route path="/runs/:runId/overview"        element={<ProtectedRoute><RunOverview /></ProtectedRoute>} />
      <Route path="/runs/:runId/extraction"      element={<ProtectedRoute><StageExtraction /></ProtectedRoute>} />
      <Route path="/runs/:runId/rdf"             element={<ProtectedRoute><StageRdf /></ProtectedRoute>} />
      <Route path="/runs/:runId/wikidata-studio" element={<ProtectedRoute><WikidataStudio /></ProtectedRoute>} />
      <Route path="/runs/:runId/hmo-studio"      element={<ProtectedRoute><HmoStudio /></ProtectedRoute>} />
      <Route path="/settings"                    element={<ProtectedRoute><Settings /></ProtectedRoute>} />
      <Route path="/admin/invites"               element={<ProtectedRoute><AdminInvites /></ProtectedRoute>} />
    </Routes>
  );
}
