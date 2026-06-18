import { type FormEvent, useEffect, useState } from "react";

import {AdminLayout} from "@/components/admin/AdminLayout";
import { api, ApiError } from "@/api/client";
import { useAuth } from "@/stores/auth";
import {Glass} from "@/components/glass";

type Role = "admin" | "editor";

interface Invite {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  accepted_at: string | null;
  created_at: string;
}

interface InviteResp extends Invite {
  name: string;
  accept_url: string;
}

export default function AdminInvites() {
  const { user } = useAuth();
  const [invites, setInvites] = useState<Invite[]>([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("editor");
  const [lastCreated, setLastCreated] = useState<InviteResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function refresh() {
    try {
      setInvites(await api.get<Invite[]>("/admin/invites"));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  if (user?.role !== "admin") {
    return (
      <AdminLayout>
        <Glass className="p-8">
          <h2 className="text-xl font-semibold">Admin only</h2>
          <p className="muted mt-2">Your role doesn't permit invitation management.</p>
        </Glass>
      </AdminLayout>
    );
  }

  async function createInvite(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const inv = await api.post<InviteResp>("/admin/invites", { email, name, role });
      setLastCreated(inv);
      setEmail("");
      setName("");
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to invite");
    } finally {
      setSubmitting(false);
    }
  }

  async function revoke(id: string) {
    try {
      await api.del(`/admin/invites/${id}`);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to revoke");
    }
  }

  return (
    <AdminLayout>
      <div className="space-y-6">
        <Glass as="section" className="p-6">
          <div className="kicker mb-1">Admin · invitations</div>
          <h2 className="text-xl font-semibold">Invite a teammate</h2>
          <form onSubmit={createInvite} className="grid md:grid-cols-4 gap-3 mt-4">
            <input
              type="email" required placeholder="email@example.com"
              value={email} onChange={(e) => setEmail(e.target.value)}
              className="input-glass md:col-span-2"
            />
            <input
              type="text" required placeholder="Full name"
              value={name} onChange={(e) => setName(e.target.value)}
              className="input-glass"
            />
            <select
              value={role} onChange={(e) => setRole(e.target.value as Role)}
              className="input-glass"
            >
              <option value="editor">editor</option>
              <option value="admin">admin</option>
            </select>
            <button type="submit" disabled={submitting} className="button-primary md:col-span-4 w-full md:w-auto md:justify-self-start">
              {submitting ? "Creating…" : "Create invitation"}
            </button>
          </form>
          {error && <p className="text-danger text-sm mt-3">{error}</p>}
        </Glass>

        {lastCreated && (
          <Glass as="section" className="p-6 space-y-2">
            <div className="kicker">Just created</div>
            <p className="text-sm">
              Send this one-time link to <b>{lastCreated.email}</b>. We never
              store it plaintext — once you close this page it can't be
              recovered, only a fresh invitation can be issued.
            </p>
            <div className="rounded-xl px-4 py-3 break-all text-sm font-mono code-surface">
              {lastCreated.accept_url}
            </div>
            <button
              onClick={() => navigator.clipboard.writeText(lastCreated.accept_url)}
              className="button-ghost text-sm"
            >
              Copy link
            </button>
          </Glass>
        )}

        <Glass as="section" className="p-6 space-y-3">
          <h3 className="text-lg font-medium">All invitations</h3>
          <table className="w-full text-sm">
            <thead className="muted text-left">
              <tr>
                <th className="py-2">Email</th>
                <th className="py-2">Role</th>
                <th className="py-2">Status</th>
                <th className="py-2">Expires</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {invites.map((inv) => (
                <tr key={inv.id} className="border-t border-white/5">
                  <td className="py-2">{inv.email}</td>
                  <td className="py-2"><span className="kicker">{inv.role}</span></td>
                  <td className="py-2">
                    {inv.accepted_at ? (
                      <span className="text-biu-sky">Accepted</span>
                    ) : new Date(inv.expires_at) < new Date() ? (
                      <span className="muted">Expired</span>
                    ) : (
                      <span>Pending</span>
                    )}
                  </td>
                  <td className="py-2 muted">{new Date(inv.expires_at).toLocaleString()}</td>
                  <td className="py-2 text-right">
                    {!inv.accepted_at && (
                      <button onClick={() => revoke(inv.id)} className="button-ghost text-xs">Revoke</button>
                    )}
                  </td>
                </tr>
              ))}
              {invites.length === 0 && (
                <tr><td colSpan={5} className="py-6 muted text-center">No invitations yet.</td></tr>
              )}
            </tbody>
          </table>
        </Glass>
      </div>
    </AdminLayout>
  );
}
