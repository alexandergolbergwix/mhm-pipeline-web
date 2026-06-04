import {useEffect, useMemo, useState} from "react";
import {Link, Navigate} from "react-router-dom";

import {AdminLayout} from "@/components/admin/AdminLayout";
import {Admin, type UserListItem} from "@/api/admin";
import {ApiError} from "@/api/client";
import {useAuth} from "@/stores/auth";

type RoleFilter = "all" | "admin" | "editor";

export default function AdminUsers() {
  const {user} = useAuth();
  const [users, setUsers] = useState<UserListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("all");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Admin.listUsers()
      .then((list) => { if (!cancelled) { setUsers(list); setLoading(false); } })
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
    return users.filter((u) => {
      if (roleFilter !== "all" && u.role !== roleFilter) return false;
      if (search.trim()) {
        const q = search.trim().toLowerCase();
        return u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q);
      }
      return true;
    });
  }, [users, roleFilter, search]);

  const ROLE_FILTERS: {value: RoleFilter; label: string}[] = [
    {value: "all", label: "All"},
    {value: "admin", label: "Admins"},
    {value: "editor", label: "Editors"},
  ];

  return (
    <AdminLayout>
      <div data-testid="admin-users-page" className="space-y-6">
        <section className="glass p-6">
          <div className="kicker mb-1">Admin · users</div>
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <h2 className="text-xl font-semibold">
              Users{" "}
              <span className="muted text-base font-normal">({users.length})</span>
            </h2>
            <Link to="/admin/invites" className="button-primary text-sm">Invite user</Link>
          </div>

          <div className="flex flex-wrap gap-3 mt-4">
            <input
              type="search"
              data-testid="user-search"
              placeholder="Search name or email…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="input-glass w-56 text-sm"
            />
            <div className="flex gap-2">
              {ROLE_FILTERS.map((f) => {
                const active = roleFilter === f.value;
                return (
                  <button
                    key={f.value}
                    type="button"
                    data-testid={`role-filter-${f.value}`}
                    onClick={() => setRoleFilter(f.value)}
                    aria-pressed={active}
                    className={`glass-pill text-xs ${active ? "border-biu-sky text-biu-sky" : "muted"}`}
                  >
                    {f.label}
                  </button>
                );
              })}
            </div>
          </div>

          {error && <p className="text-red-300 text-sm mt-3">{error}</p>}
        </section>

        <section className="glass p-6">
          <table className="w-full text-sm">
            <thead className="muted text-left">
              <tr>
                <th className="py-2">Name</th>
                <th className="py-2">Email</th>
                <th className="py-2">Role</th>
                <th className="py-2">Projects</th>
                <th className="py-2">Joined</th>
                <th className="py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((u) => (
                <tr key={u.id} data-testid={`user-row-${u.id}`} className="border-t border-white/5">
                  <td className="py-2 font-medium">{u.name}</td>
                  <td className="py-2 muted">{u.email}</td>
                  <td className="py-2">
                    <span
                      className={`glass-pill text-xs ${
                        u.role === "admin"
                          ? "border-biu-sky text-biu-sky bg-sky-500/10"
                          : "muted"
                      }`}
                    >
                      {u.role}
                    </span>
                  </td>
                  <td className="py-2 muted">{u.project_count}</td>
                  <td className="py-2 muted">{new Date(u.created_at).toLocaleDateString()}</td>
                  <td className="py-2 text-right">
                    <Link to={`/admin/users/${u.id}`} className="button-ghost text-xs">
                      View
                    </Link>
                  </td>
                </tr>
              ))}
              {visible.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-6 muted text-center">
                    {loading ? "Loading…" : "No users match."}
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
