import {useEffect, useState} from "react";
import {Link, Navigate} from "react-router-dom";

import {AdminLayout} from "@/components/admin/AdminLayout";
import {AdminStatCard} from "@/components/admin/AdminStatCard";
import {Admin, type AdminStats} from "@/api/admin";
import {AccessRequests, type AccessRequestListItem} from "@/api/accessRequests";
import {ApiError} from "@/api/client";
import {useAuth} from "@/stores/auth";
import {Glass} from "@/components/glass";

export default function AdminDashboard() {
  const {user} = useAuth();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [pending, setPending] = useState<AccessRequestListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      const [s, p] = await Promise.all([
        Admin.getStats(),
        AccessRequests.list("pending_admin"),
      ]);
      setStats(s);
      setPending(p);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    if (user?.role === "admin") {
      Admin.getStats()
        .then((s) => { if (!cancelled) setStats(s); })
        .catch(() => {});
      AccessRequests.list("pending_admin")
        .then((p) => { if (!cancelled) setPending(p); })
        .catch((e: unknown) => {
          if (!cancelled) setError(e instanceof ApiError ? e.detail : String(e));
        })
        .finally(() => { if (!cancelled) setLoading(false); });
    }
    return () => { cancelled = true; };
  }, [user?.role]);

  if (user && user.role !== "admin") return <Navigate to="/" replace />;

  async function handleApprove(id: string) {
    setApprovingId(id);
    setError(null);
    setNotice(null);
    try {
      await AccessRequests.approve(id);
      setNotice("Request approved.");
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to approve");
    } finally {
      setApprovingId(null);
    }
  }

  return (
    <AdminLayout>
      <div data-testid="admin-dashboard" className="space-y-6">
        <Glass as="section" className="p-6">
          <div className="kicker mb-1">Admin</div>
          <h2 className="text-xl font-semibold">Dashboard</h2>
          {error && <p className="text-danger text-sm mt-3">{error}</p>}
          {notice && <p className="text-green-300 text-sm mt-3">{notice}</p>}
        </Glass>

        <div className="grid grid-cols-2 gap-4">
          <AdminStatCard
            label="Pending Requests"
            value={stats?.pending_access_requests ?? "—"}
            badge={stats?.pending_access_requests}
            href="/admin/access-requests"
          />
          <AdminStatCard
            label="Total Users"
            value={stats?.total_users ?? "—"}
            href="/admin/users"
          />
          <AdminStatCard
            label="Projects"
            value={stats?.total_projects ?? "—"}
            href="/admin/projects"
          />
          <AdminStatCard
            label="Active Invites"
            value={stats?.active_invitations ?? "—"}
            href="/admin/invites"
          />
        </div>

        <Glass as="section" className="p-6 space-y-4">
          <div className="flex items-center justify-between gap-4">
            <h3 className="text-lg font-medium">Pending approvals</h3>
            <Link to="/admin/access-requests" className="button-ghost text-xs">View all</Link>
          </div>

          {loading ? (
            <p className="muted text-sm">Loading…</p>
          ) : pending.length === 0 ? (
            <p className="muted text-sm">No pending requests.</p>
          ) : (
            <ul className="space-y-3">
              {pending.map((req) => (
                <li key={req.id} className="flex items-center justify-between gap-3 border-t border-white/5 pt-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{req.name}</p>
                    <p className="muted text-xs truncate">{req.email}</p>
                    {req.affiliation && (
                      <p className="muted text-xs truncate">{req.affiliation}</p>
                    )}
                  </div>
                  <button
                    type="button"
                    data-testid={`dashboard-approve-${req.id}`}
                    disabled={approvingId === req.id}
                    onClick={() => void handleApprove(req.id)}
                    className="button-primary text-xs bg-green-500/80 hover:bg-green-500 border-green-400/60 flex-shrink-0"
                  >
                    {approvingId === req.id ? "…" : "Approve"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Glass>
      </div>
    </AdminLayout>
  );
}
