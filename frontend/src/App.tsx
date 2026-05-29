import { useEffect } from "react";
import { Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "@/components/ProtectedRoute";
import AcceptInvite from "@/routes/AcceptInvite";
import AdminInvites from "@/routes/AdminInvites";
import ForgotPassword from "@/routes/ForgotPassword";
import Home from "@/routes/Home";
import Login from "@/routes/Login";
import ResetPassword from "@/routes/ResetPassword";
import Settings from "@/routes/Settings";
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
      <Route path="/"                element={<ProtectedRoute><Home /></ProtectedRoute>} />
      <Route path="/settings"        element={<ProtectedRoute><Settings /></ProtectedRoute>} />
      <Route path="/admin/invites"   element={<ProtectedRoute><AdminInvites /></ProtectedRoute>} />
    </Routes>
  );
}
