import type {ReactNode} from "react";
import {Navigate} from "react-router-dom";

import {ProtectedRoute} from "@/components/ProtectedRoute";
import {useAuth} from "@/stores/auth";

function AdminGate({children}: {children: ReactNode}) {
  const user = useAuth(state => state.user);
  if (user?.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

export function AdminRoute({children}: {children: ReactNode}) {
  return <ProtectedRoute><AdminGate>{children}</AdminGate></ProtectedRoute>;
}
