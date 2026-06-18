import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { AccessRequests } from "@/api/accessRequests";
import { ApiError } from "@/api/client";
import {Glass} from "@/components/glass";

type ConfirmStatus = "confirmed" | "expired" | "already_used";

interface ConfirmResult {
  status: ConfirmStatus;
}

const MESSAGES: Record<ConfirmStatus, string> = {
  confirmed:
    "Thank you. Your request is now awaiting admin review. You'll receive an email once a decision is made.",
  expired: "This confirmation link has expired. Please submit a new request.",
  already_used: "This request has already been confirmed.",
};

const HEADINGS: Record<ConfirmStatus, string> = {
  confirmed: "Request confirmed",
  expired: "Link expired",
  already_used: "Already confirmed",
};

export default function ConfirmRequest() {
  const { token } = useParams<{ token: string }>();
  const [status, setStatus] = useState<ConfirmStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    if (!token) {
      setError("Missing confirmation token.");
      setLoading(false);
      return;
    }

    AccessRequests.confirm(token)
      .then((result: ConfirmResult) => {
        if (cancelled) {
          return;
        }
        setStatus(result.status);
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        setError(
          err instanceof ApiError ? err.detail : "Could not confirm request.",
        );
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <div
      data-testid="confirm-page"
      className="grid place-items-center min-h-screen px-4"
    >
      <Glass className="p-8 w-full max-w-sm space-y-5">
        <header className="space-y-1">
          <div className="kicker">Access request</div>
          <h1 className="text-2xl font-semibold">
            {loading
              ? "Confirming your request…"
              : status
                ? HEADINGS[status]
                : "Confirmation failed"}
          </h1>
        </header>

        {loading && (
          <p className="text-sm muted" data-testid="confirm-loading">
            One moment while we verify your link.
          </p>
        )}

        {!loading && status && (
          <div
            data-testid={`confirm-status-${status}`}
            className="space-y-3"
          >
            <p data-testid="confirm-message" className="text-sm text-ink">
              {MESSAGES[status]}
            </p>
          </div>
        )}

        {!loading && !status && error && (
          <p className="text-danger text-sm" data-testid="confirm-error">
            {error}
          </p>
        )}
      </Glass>
    </div>
  );
}
