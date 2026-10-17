import {useEffect, useState} from "react";

import type {
  PublicationForeignQidConsent,
  PublicationTarget,
} from "@/api/publication";

export interface BlockedActionStatement {
  property?: string | null;
  value?: unknown;
  value_type?: string | null;
}

interface RemoteClaim {
  property: string;
  value: string;
}

export interface WikidataBlockedActionDrawerProps {
  entityKey: string;
  targetQid: string | null;
  consent: PublicationForeignQidConsent | null;
  ourStatements: BlockedActionStatement[];
  target: PublicationTarget;
  disabled: boolean;
  onUseExisting: ((entityKeys: string[]) => void | Promise<void>) | null;
  onAcceptUpdate: (consent: PublicationForeignQidConsent) => void | Promise<void>;
}

function formatClaimValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.id === "string") return record.id;
    if (typeof record.time === "string") return record.time;
    if (typeof record.text === "string") return record.text;
  }
  return JSON.stringify(value);
}

function ourClaimView(statement: BlockedActionStatement): RemoteClaim {
  return {
    property: String(statement.property ?? ""),
    value: formatClaimValue(statement.value),
  };
}

function remoteClaimsFrom(payload: unknown, qid: string): RemoteClaim[] {
  const root = payload as {entities?: Record<string, {claims?: Record<string, Array<{mainsnak?: {datavalue?: {value?: unknown}}>>}>>};
  const entity = root.entities?.[qid];
  const claims = entity?.claims ?? {};
  const rows: RemoteClaim[] = [];
  for (const [property, snaks] of Object.entries(claims)) {
    for (const snak of snaks ?? []) {
      const value = formatClaimValue(snak?.mainsnak?.datavalue?.value);
      if (value) rows.push({property, value});
    }
  }
  return rows;
}

export function WikidataBlockedActionDrawer({
  entityKey,
  targetQid,
  consent,
  ourStatements,
  target,
  disabled,
  onUseExisting,
  onAcceptUpdate,
}: WikidataBlockedActionDrawerProps) {
  const [open, setOpen] = useState(false);
  const [remoteClaims, setRemoteClaims] = useState<RemoteClaim[] | null>(null);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<"" | "reuse" | "accept">("");

  useEffect(() => {
    if (!open || !targetQid || remoteClaims !== null || remoteError !== null) return;
    let stopped = false;
    const site = target === "live" ? "www.wikidata.org" : "test.wikidata.org";
    void fetch(
      `https://${site}/w/api.php?action=wbgetentities&ids=${encodeURIComponent(targetQid)}&props=claims&format=json&origin=*`,
    )
      .then((response) => response.json())
      .then((payload: unknown) => {
        if (!stopped) setRemoteClaims(remoteClaimsFrom(payload, targetQid));
      })
      .catch(() => {
        if (!stopped) setRemoteError("The existing item could not be loaded for comparison. Open its page to compare manually.");
      });
    return () => {
      stopped = true;
    };
  }, [open, remoteClaims, remoteError, target, targetQid]);

  if (!targetQid) {
    return (
      <p className="mt-2 muted" data-testid="publication-accept-unavailable">
        No remote item is recorded for this decision. Review the record in Wikidata Studio.
      </p>
    );
  }

  const ours = ourStatements.map(ourClaimView).filter((claim) => claim.property && claim.value);
  const theirs = remoteClaims ?? [];
  const shared = ours.filter((claim) => theirs.some((row) => row.property === claim.property && row.value === claim.value));
  const onlyOurs = ours.filter((claim) => !shared.includes(claim));
  const onlyTheirs = theirs.filter((claim) => !ours.some((row) => row.property === claim.property && row.value === claim.value));
  const wikiHref = `https://${target === "live" ? "www" : "test"}.wikidata.org/wiki/${targetQid}`;

  const reuse = async () => {
    if (!onUseExisting) return;
    setBusyAction("reuse");
    try {
      await onUseExisting([entityKey]);
    } finally {
      setBusyAction("");
    }
  };
  const accept = async () => {
    if (!consent) return;
    setBusyAction("accept");
    try {
      await onAcceptUpdate(consent);
    } finally {
      setBusyAction("");
    }
  };

  return (
    <div className="mt-2 space-y-1" data-testid="publication-accept-existing">
      <a href={wikiHref} target="_blank" rel="noopener noreferrer" className="link-accent underline">
        Review {targetQid}
      </a>
      <button
        type="button"
        className="button-ghost text-sm"
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
        data-testid="publication-accept-existing-toggle"
      >
        {open ? "Close the item comparison" : "Accept existing item"}
      </button>
      {open && (
        <div className="rounded-lg border border-white/10 p-2 space-y-2" data-testid="publication-accept-diff">
          <p className="muted">Compare our planned claims with the claims on {targetQid} before you accept it.</p>
          {remoteError && <p className="text-warn">{remoteError}</p>}
          {!remoteClaims && !remoteError && <p className="muted">Loading the existing item…</p>}
          {!!shared.length && (
            <div>
              <p className="font-semibold">Same on both ({shared.length})</p>
              <ul className="text-xs">
                {shared.map((claim, index) => (
                  <li key={`shared-${claim.property}-${index}`}>{claim.property}: {claim.value}</li>
                ))}
              </ul>
            </div>
          )}
          {!!onlyOurs.length && (
            <div>
              <p className="font-semibold">New for the existing item ({onlyOurs.length})</p>
              <ul className="text-xs">
                {onlyOurs.map((claim, index) => (
                  <li key={`ours-${claim.property}-${index}`}>{claim.property}: {claim.value}</li>
                ))}
              </ul>
            </div>
          )}
          {!!onlyTheirs.length && (
            <div>
              <p className="font-semibold">Already on the existing item ({onlyTheirs.length})</p>
              <ul className="text-xs">
                {onlyTheirs.map((claim, index) => (
                  <li key={`theirs-${claim.property}-${index}`}>{claim.property}: {claim.value}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            {onUseExisting && (
              <button
                type="button"
                className="button-ghost text-sm"
                disabled={disabled || busyAction !== ""}
                onClick={() => { void reuse(); }}
                data-testid="publication-reuse-existing"
              >
                {busyAction === "reuse" ? "Saving…" : `Use ${targetQid} without updates`}
              </button>
            )}
            <button
              type="button"
              className="button-ghost text-sm"
              disabled={disabled || !consent || busyAction !== ""}
              onClick={() => { void accept(); }}
              data-testid="publication-accept-foreign-update"
            >
              {busyAction === "accept" ? "Saving…" : `Accept & update ${targetQid}`}
            </button>
            <button
              type="button"
              className="button-ghost text-sm"
              disabled={disabled || busyAction !== ""}
              onClick={() => setOpen(false)}
              data-testid="publication-defer-foreign"
            >
              Leave it out of this release
            </button>
          </div>
          <p className="muted">
            Accept & update writes your consent for this Release to add our claims to {targetQid}.
            Leave it out keeps the record for a later release.
          </p>
        </div>
      )}
    </div>
  );
}
