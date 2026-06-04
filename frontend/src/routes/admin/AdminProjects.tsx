import {useEffect, useMemo, useState} from "react";
import {Navigate} from "react-router-dom";

import {AdminLayout} from "@/components/admin/AdminLayout";
import {Admin, type ProjectListItem, type UserListItem} from "@/api/admin";
import {ApiError} from "@/api/client";
import {useAuth} from "@/stores/auth";

export default function AdminProjects() {
  const {user} = useAuth();
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [users, setUsers] = useState<UserListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const [transferProjectId, setTransferProjectId] = useState<string | null>(null);
  const [transferNewOwnerId, setTransferNewOwnerId] = useState("");
  const [transferSubmitting, setTransferSubmitting] = useState(false);

  async function refreshProjects() {
    try {
      const list = await Admin.listProjects();
      setProjects(list);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([Admin.listProjects(), Admin.listUsers()])
      .then(([ps, us]) => {
        if (!cancelled) {
          setProjects(ps);
          setUsers(us);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof ApiError ? e.detail : String(e));
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, []);

  if (user && user.role !== "admin") return <Navigate to="/" replace />;

  const visible = useMemo(() => {
    if (!search.trim()) return projects;
    const q = search.trim().toLowerCase();
    return projects.filter(
      (p) => p.name.toLowerCase().includes(q) || p.owner_email.toLowerCase().includes(q),
    );
  }, [projects, search]);

  const transferProject = projects.find((p) => p.id === transferProjectId);

  async function handleTransfer() {
    if (!transferProjectId || !transferNewOwnerId) return;
    setTransferSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      await Admin.transferProject(transferProjectId, transferNewOwnerId);
      setNotice("Ownership transferred successfully.");
      setTransferProjectId(null);
      setTransferNewOwnerId("");
      await refreshProjects();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to transfer ownership");
    } finally {
      setTransferSubmitting(false);
    }
  }

  return (
    <AdminLayout>
      <div data-testid="admin-projects-page" className="space-y-6">
        <section className="glass p-6">
          <div className="kicker mb-1">Admin · projects</div>
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <h2 className="text-xl font-semibold">
              Projects{" "}
              <span className="muted text-base font-normal">({projects.length})</span>
            </h2>
          </div>
          <div className="mt-4">
            <input
              type="search"
              data-testid="project-search"
              placeholder="Search by name or owner email…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="input-glass w-72 text-sm"
            />
          </div>
          {error && <p className="text-red-300 text-sm mt-3">{error}</p>}
          {notice && <p className="text-green-300 text-sm mt-3">{notice}</p>}
        </section>

        <section className="glass p-6">
          <table className="w-full text-sm">
            <thead className="muted text-left">
              <tr>
                <th className="py-2">Name</th>
                <th className="py-2">Owner</th>
                <th className="py-2">Members</th>
                <th className="py-2">Created</th>
                <th className="py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((p) => (
                <>
                  <tr key={p.id} data-testid={`project-row-${p.id}`} className="border-t border-white/5">
                    <td className="py-2 font-medium">{p.name}</td>
                    <td className="py-2 muted">
                      <div>{p.owner_name}</div>
                      <div className="text-xs opacity-70">{p.owner_email}</div>
                    </td>
                    <td className="py-2 muted">{p.member_count}</td>
                    <td className="py-2 muted">{new Date(p.created_at).toLocaleDateString()}</td>
                    <td className="py-2 text-right">
                      <button
                        type="button"
                        data-testid={`transfer-button-${p.id}`}
                        onClick={() => {
                          setTransferProjectId(p.id === transferProjectId ? null : p.id);
                          setTransferNewOwnerId("");
                          setError(null);
                        }}
                        className="button-ghost text-xs"
                      >
                        Transfer ownership
                      </button>
                    </td>
                  </tr>
                  {transferProjectId === p.id && (
                    <tr key={`${p.id}-transfer`} className="border-t border-white/5 bg-black/20">
                      <td colSpan={5} className="py-3 px-2">
                        <div className="flex items-center gap-3 flex-wrap">
                          <span className="text-sm muted">
                            Transfer <b className="text-ink">{transferProject?.name}</b> to:
                          </span>
                          <select
                            data-testid="transfer-owner-select"
                            value={transferNewOwnerId}
                            onChange={(e) => setTransferNewOwnerId(e.target.value)}
                            className="input-glass text-sm flex-1 min-w-48"
                          >
                            <option value="">Select new owner…</option>
                            {users
                              .filter((u) => u.id !== p.owner_id)
                              .map((u) => (
                                <option key={u.id} value={u.id}>
                                  {u.name} ({u.email})
                                </option>
                              ))}
                          </select>
                          <button
                            type="button"
                            data-testid="confirm-transfer-button"
                            disabled={!transferNewOwnerId || transferSubmitting}
                            onClick={() => void handleTransfer()}
                            className="button-primary text-sm"
                          >
                            {transferSubmitting ? "Transferring…" : "Confirm transfer"}
                          </button>
                          <button
                            type="button"
                            onClick={() => { setTransferProjectId(null); setTransferNewOwnerId(""); }}
                            className="button-ghost text-sm"
                          >
                            Cancel
                          </button>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
              {visible.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-6 muted text-center">
                    {loading ? "Loading…" : "No projects match."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      </div>
    </AdminLayout>
  );
}
