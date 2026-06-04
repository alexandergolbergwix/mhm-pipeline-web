import { type FormEvent, useEffect, useMemo, useState } from "react";
import { Navigate } from "react-router-dom";

import {AdminLayout} from "@/components/admin/AdminLayout";
import { ApiError } from "@/api/client";
import {
  AccessRequests,
  type AccessRequestListItem,
  type AccessRequestStatus,
} from "@/api/accessRequests";
import { useAuth } from "@/stores/auth";

type FilterValue = "all" | AccessRequestStatus;

const FILTERS: { value: FilterValue; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pending_email_confirm", label: "Pending email confirm" },
  { value: "pending_admin", label: "Pending admin" },
  { value: "approved", label: "Approved" },
  { value: "denied", label: "Denied" },
];

function statusPillClasses(status: AccessRequestStatus): string {
  switch (status) {
    case "approved":
      return "border-green-400/60 text-green-300 bg-green-500/10";
    case "denied":
      return "border-red-400/60 text-red-300 bg-red-500/10";
    case "pending_admin":
    case "pending_email_confirm":
    default:
      return "border-yellow-400/60 text-yellow-300 bg-yellow-500/10";
  }
}

function statusLabel(status: AccessRequestStatus): string {
  switch (status) {
    case "pending_email_confirm":
      return "Pending email confirm";
    case "pending_admin":
      return "Pending admin";
    case "approved":
      return "Approved";
    case "denied":
      return "Denied";
    default:
      return status;
  }
}

interface DenyModalProps {
  open: boolean;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}

function DenyModal({ open, submitting, onCancel, onConfirm }: DenyModalProps) {
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (open) setReason("");
  }, [open]);

  if (!open) return null;

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = reason.trim();
    if (trimmed.length < 1) return;
    onConfirm(trimmed);
  }

  return (
    <div
      data-testid="deny-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <form onSubmit={handleSubmit} className="glass p-6 w-full max-w-md space-y-4">
        <div>
          <div className="kicker mb-1">Deny request</div>
          <h3 className="text-lg font-semibold">Reason for denial</h3>
          <p className="muted text-sm mt-1">
            Shared with the requester in the denial email. Keep it short and respectful.
          </p>
        </div>
        <textarea
          data-testid="deny-reason-input"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          required
          minLength={1}
          rows={4}
          autoFocus
          placeholder="e.g. We currently only onboard project collaborators."
          className="input-glass w-full resize-none"
        />
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="button-ghost text-sm"
          >
            Cancel
          </button>
          <button
            type="submit"
            data-testid="deny-confirm-button"
            disabled={submitting || reason.trim().length < 1}
            className="button-primary text-sm bg-red-500/80 hover:bg-red-500 border-red-400/60"
          >
            {submitting ? "Denying…" : "Confirm deny"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function AccessRequestsQueue() {
  const { user } = useAuth();
  const [requests, setRequests] = useState<AccessRequestListItem[]>([]);
  const [filter, setFilter] = useState<FilterValue>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [actingId, setActingId] = useState<string | null>(null);
  const [denyTargetId, setDenyTargetId] = useState<string | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const list = await AccessRequests.list();
      setRequests(list);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (user?.role === "admin") {
      void refresh();
    }
  }, [user?.role]);

  const counts = useMemo(() => {
    const c: Record<FilterValue, number> = {
      all: requests.length,
      pending_email_confirm: 0,
      pending_admin: 0,
      approved: 0,
      denied: 0,
    };
    for (const r of requests) {
      c[r.status] = (c[r.status] ?? 0) + 1;
    }
    return c;
  }, [requests]);

  const visible = useMemo(() => {
    if (filter === "all") return requests;
    return requests.filter((r) => r.status === filter);
  }, [requests, filter]);

  if (user && user.role !== "admin") {
    return <Navigate to="/" replace />;
  }

  async function handleApprove(id: string) {
    setActingId(id);
    setError(null);
    setNotice(null);
    try {
      await AccessRequests.approve(id);
      setNotice("Request approved. The requester will receive an invitation email.");
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to approve");
    } finally {
      setActingId(null);
    }
  }

  async function handleDeny(id: string, reason: string) {
    setActingId(id);
    setError(null);
    setNotice(null);
    try {
      await AccessRequests.deny(id, reason);
      setNotice("Request denied. The requester has been notified.");
      setDenyTargetId(null);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to deny");
    } finally {
      setActingId(null);
    }
  }

  return (
    <AdminLayout>
      <div data-testid="access-requests-page" className="space-y-6">
        <section className="glass p-6">
          <div className="kicker mb-1">Admin · access</div>
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <h2 className="text-xl font-semibold">
              Access requests{" "}
              <span className="muted text-base font-normal">({requests.length})</span>
            </h2>
            <button onClick={() => void refresh()} className="button-ghost text-xs" disabled={loading}>
              {loading ? "Refreshing…" : "Refresh"}
            </button>
          </div>

          <div className="flex flex-wrap gap-2 mt-4">
            {FILTERS.map((f) => {
              const active = filter === f.value;
              return (
                <button
                  type="button"
                  key={f.value}
                  data-testid={`filter-chip-${f.value}`}
                  data-active={active}
                  onClick={() => setFilter(f.value)}
                  aria-pressed={active}
                  className={`glass-pill text-xs ${active ? "border-biu-sky text-biu-sky" : "muted"}`}
                >
                  {f.label}
                  <span className="ml-1 opacity-70">({counts[f.value] ?? 0})</span>
                </button>
              );
            })}
          </div>

          {error && <p className="text-red-300 text-sm mt-3">{error}</p>}
          {notice && <p className="text-green-300 text-sm mt-3">{notice}</p>}
        </section>

        <section className="glass p-6 space-y-3">
          <table className="w-full text-sm">
            <thead className="muted text-left">
              <tr>
                <th className="py-2">Submitted</th>
                <th className="py-2">Name</th>
                <th className="py-2">Email</th>
                <th className="py-2">Affiliation</th>
                <th className="py-2">Status</th>
                <th className="py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((req) => {
                const isExpanded = expandedId === req.id;
                const canActOnRequest = req.status === "pending_admin";
                const isActing = actingId === req.id;
                return (
                  <>
                    <tr
                      key={req.id}
                      data-testid={`request-row-${req.id}`}
                      onClick={() =>
                        setExpandedId(isExpanded ? null : req.id)
                      }
                      className="border-t border-white/5 cursor-pointer hover:bg-white/5"
                    >
                      <td className="py-2 muted">
                        {new Date(req.created_at).toLocaleString()}
                      </td>
                      <td className="py-2">{req.name}</td>
                      <td className="py-2">{req.email}</td>
                      <td className="py-2 muted">{req.affiliation}</td>
                      <td className="py-2">
                        <span
                          className={`glass-pill text-xs ${statusPillClasses(req.status)}`}
                        >
                          {statusLabel(req.status)}
                        </span>
                      </td>
                      <td className="py-2 text-right" onClick={(e) => e.stopPropagation()}>
                        {canActOnRequest ? (
                          <div className="flex justify-end gap-2">
                            <button
                              type="button"
                              data-testid={`approve-button-${req.id}`}
                              disabled={isActing}
                              onClick={() => void handleApprove(req.id)}
                              className="button-primary text-xs bg-green-500/80 hover:bg-green-500 border-green-400/60"
                            >
                              {isActing ? "…" : "Approve"}
                            </button>
                            <button
                              type="button"
                              data-testid={`deny-button-${req.id}`}
                              disabled={isActing}
                              onClick={() => setDenyTargetId(req.id)}
                              className="button-primary text-xs bg-red-500/80 hover:bg-red-500 border-red-400/60"
                            >
                              Deny
                            </button>
                          </div>
                        ) : (
                          <span className="muted text-xs">—</span>
                        )}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr
                        key={`${req.id}-detail`}
                        className="border-t border-white/5 bg-black/20"
                      >
                        <td colSpan={6} className="py-3 px-1">
                          <ExpandedDetail id={req.id} fallback={req} />
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
              {visible.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-6 muted text-center">
                    {loading ? "Loading…" : "No requests match this filter."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      </div>

      <DenyModal
        open={denyTargetId !== null}
        submitting={actingId === denyTargetId && denyTargetId !== null}
        onCancel={() => setDenyTargetId(null)}
        onConfirm={(reason) => {
          if (denyTargetId) void handleDeny(denyTargetId, reason);
        }}
      />
    </AdminLayout>
  );
}

interface ExpandedDetailProps {
  id: string;
  fallback: AccessRequestListItem;
}

function ExpandedDetail({ id, fallback }: ExpandedDetailProps) {
  const [justification, setJustification] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    AccessRequests.get(id)
      .then((detail) => {
        if (!cancelled) {
          setJustification(detail.justification);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setLoadError(e instanceof ApiError ? e.detail : String(e));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <div className="space-y-2 text-sm px-3">
      <div>
        <div className="kicker mb-1">Justification</div>
        {loading ? (
          <p className="muted">Loading…</p>
        ) : loadError ? (
          <p className="text-red-300">{loadError}</p>
        ) : (
          <p className="whitespace-pre-wrap">{justification ?? "(none provided)"}</p>
        )}
      </div>
      {fallback.denial_reason && (
        <div>
          <div className="kicker mb-1">Denial reason</div>
          <p className="whitespace-pre-wrap text-red-200">{fallback.denial_reason}</p>
        </div>
      )}
      {fallback.reviewed_at && (
        <div className="muted text-xs">
          Reviewed {new Date(fallback.reviewed_at).toLocaleString()}
          {fallback.reviewed_by_email ? ` by ${fallback.reviewed_by_email}` : ""}
        </div>
      )}
    </div>
  );
}
