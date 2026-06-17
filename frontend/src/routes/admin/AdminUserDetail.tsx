import {useEffect, useState} from "react";
import {Link, Navigate, useNavigate, useParams} from "react-router-dom";

import {AdminLayout} from "@/components/admin/AdminLayout";
import {ConfirmDestructiveDialog} from "@/components/admin/ConfirmDestructiveDialog";
import {Admin, type UserDetail} from "@/api/admin";
import {ApiError} from "@/api/client";
import {useAuth} from "@/stores/auth";
import {Glass, GlassPill} from "@/components/glass";

export default function AdminUserDetail() {
  const {user: currentUser} = useAuth();
  const {userId} = useParams<{userId: string}>();
  const navigate = useNavigate();

  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [selectedRole, setSelectedRole] = useState<"admin" | "editor">("editor");
  const [showRoleConfirm, setShowRoleConfirm] = useState(false);
  const [roleSubmitting, setRoleSubmitting] = useState(false);

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);

  const isSelf = currentUser?.id === userId;

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    setLoading(true);
    Admin.getUser(userId)
      .then((d) => {
        if (!cancelled) {
          setDetail(d);
          setSelectedRole(d.role);
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
  }, [userId]);

  if (currentUser && currentUser.role !== "admin") return <Navigate to="/" replace />;

  async function handleRoleChange(newRole: "admin" | "editor") {
    if (!userId || !detail) return;
    setRoleSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await Admin.updateUserRole(userId, newRole);
      setDetail({...detail, role: updated.role});
      setSelectedRole(updated.role);
      setNotice(`Role updated to ${updated.role}.`);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to update role");
    } finally {
      setRoleSubmitting(false);
      setShowRoleConfirm(false);
    }
  }

  function onRoleSelectChange(newRole: "admin" | "editor") {
    setSelectedRole(newRole);
    if (detail?.role === "admin" && newRole === "editor") {
      setShowRoleConfirm(true);
    } else {
      void handleRoleChange(newRole);
    }
  }

  async function handleInvalidateSessions() {
    if (!userId) return;
    setError(null);
    setNotice(null);
    try {
      await Admin.invalidateSessions(userId);
      setNotice("All sessions invalidated. The user will be signed out on next request.");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to invalidate sessions");
    }
  }

  async function handleDelete() {
    if (!userId) return;
    setDeleteSubmitting(true);
    setError(null);
    try {
      await Admin.deleteUser(userId);
      navigate("/admin/users");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to delete user");
      setDeleteSubmitting(false);
      setShowDeleteConfirm(false);
    }
  }

  if (currentUser && currentUser.role !== "admin") return <Navigate to="/" replace />;

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <Link to="/admin/users" className="button-ghost text-xs">← Back to users</Link>
        </div>

        {loading && (
          <Glass as="section" className="p-6">
            <p className="muted text-sm">Loading…</p>
          </Glass>
        )}

        {!loading && detail && (
          <div data-testid="admin-user-detail">
            <Glass as="section" className="p-6 space-y-4">
              <div className="kicker mb-1">Admin · users</div>
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                  <h2 className="text-xl font-semibold">{detail.name}</h2>
                  <p className="muted text-sm mt-0.5">{detail.email}</p>
                  <div className="flex items-center gap-2 mt-2">
                    <GlassPill className={`text-xs ${
 detail.role === "admin"
 ? "border-biu-sky text-biu-sky bg-sky-500/10"
 : "muted"
 }`}>
                      {detail.role}
                    </GlassPill>
                    <span className="muted text-xs">
                      Joined {new Date(detail.created_at).toLocaleDateString()}
                    </span>
                    <span className="muted text-xs">
                      · {detail.active_session_count} active session{detail.active_session_count !== 1 ? "s" : ""}
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <label className="muted text-sm" htmlFor="role-select">Role</label>
                  <select
                    id="role-select"
                    data-testid="role-select"
                    value={selectedRole}
                    disabled={isSelf || roleSubmitting}
                    onChange={(e) => onRoleSelectChange(e.target.value as "admin" | "editor")}
                    className="input-glass text-sm"
                    title={isSelf ? "You cannot change your own role" : undefined}
                  >
                    <option value="editor">editor</option>
                    <option value="admin">admin</option>
                  </select>
                </div>
              </div>

              {error && <p className="text-red-300 text-sm mt-3">{error}</p>}
              {notice && <p className="text-green-300 text-sm mt-3">{notice}</p>}
            </Glass>

            <Glass as="section" className="p-6 space-y-3">
              <h3 className="text-lg font-medium">Project memberships</h3>
              {detail.memberships.length === 0 ? (
                <p className="muted text-sm">No project memberships.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead className="muted text-left">
                    <tr>
                      <th className="py-2">Project</th>
                      <th className="py-2">Role</th>
                      <th className="py-2">Joined</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.memberships.map((m) => (
                      <tr key={m.project_id} className="border-t border-white/5">
                        <td className="py-2">{m.project_name}</td>
                        <td className="py-2 muted">{m.role}</td>
                        <td className="py-2 muted">{new Date(m.joined_at).toLocaleDateString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Glass>

            <Glass as="section" className="p-6 space-y-3">
              <h3 className="text-lg font-medium">Actions</h3>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  data-testid="force-logout-button"
                  onClick={() => void handleInvalidateSessions()}
                  className="button-ghost text-sm"
                >
                  Force logout
                </button>
                <button
                  type="button"
                  data-testid="delete-user-button"
                  disabled={isSelf}
                  onClick={() => setShowDeleteConfirm(true)}
                  title={isSelf ? "You cannot delete your own account" : undefined}
                  className="button-primary text-sm bg-red-500/80 hover:bg-red-500 border-red-400/60 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  Delete user
                </button>
              </div>
            </Glass>
          </div>
        )}
      </div>

      <ConfirmDestructiveDialog
        open={showRoleConfirm}
        title="Demote admin to editor"
        description={`This will remove admin privileges from <b>${detail?.name}</b>. They will no longer be able to access the admin panel.`}
        confirmLabel="Demote to editor"
        submitting={roleSubmitting}
        onCancel={() => { setShowRoleConfirm(false); setSelectedRole(detail?.role ?? "editor"); }}
        onConfirm={() => void handleRoleChange("editor")}
      />

      <ConfirmDestructiveDialog
        open={showDeleteConfirm}
        title="Delete user"
        description={`This will permanently delete <b>${detail?.name}</b> (${detail?.email}) and remove them from all projects. This cannot be undone.`}
        confirmLabel="Delete user"
        submitting={deleteSubmitting}
        onCancel={() => setShowDeleteConfirm(false)}
        onConfirm={() => void handleDelete()}
      />
    </AdminLayout>
  );
}
