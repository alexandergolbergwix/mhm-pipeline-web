import type {HmoResolvedClaim} from "@/api/hmoStudioItems";

type AuthorityKind = "Wikidata" | "VIAF" | "Mazal" | "KIMA" | "Authority";

interface AuthorityClaim {
  kind: AuthorityKind;
  property: string;
  value: string;
  href: string | null;
}

function claimValue(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function authorityKind(property: string, value: string): AuthorityKind | null {
  const normalized = property.toLowerCase();
  const valueLower = value.toLowerCase();
  if (normalized.includes("wikidata")) return "Wikidata";
  if (normalized.includes("viaf")) return "VIAF";
  if (normalized.includes("mazal")) return "Mazal";
  if (normalized.includes("kima")) return "KIMA";
  if (normalized.includes("authority")) return "Authority";
  if (valueLower.includes("wikidata.org/entity/q")) return "Wikidata";
  if (valueLower.includes("viaf.org/viaf/")) return "VIAF";
  return null;
}

function authorityHref(kind: AuthorityKind, value: string): string | null {
  if (kind === "Wikidata") {
    const qid = value.match(/(?:entity\/|\/wiki\/)?(Q\d+)$/i)?.[1];
    return qid ? `https://www.wikidata.org/wiki/${qid.toUpperCase()}` : value.startsWith("http") ? value : null;
  }
  if (kind === "VIAF") {
    const id = value.match(/(?:viaf\/|^)(\d+)$/i)?.[1];
    return id ? `https://viaf.org/viaf/${id}` : value.startsWith("http") ? value : null;
  }
  return value.startsWith("http") ? value : null;
}

function authorityClaims(claims: HmoResolvedClaim[]): AuthorityClaim[] {
  const seen = new Set<string>();
  const result: AuthorityClaim[] = [];
  for (const claim of claims) {
    const property = claim.property_id.trim();
    const value = claimValue(claim.value);
    const kind = authorityKind(property, value ?? "");
    if (!kind || !value) continue;
    const key = `${kind}:${value}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({kind, property, value, href: authorityHref(kind, value)});
  }
  return result;
}

export function HmoAuthorityEvidence({claims}: {claims: HmoResolvedClaim[]}) {
  const entries = authorityClaims(claims);
  return (
    <section className="space-y-2 border-t border-white/5 pt-3" data-testid="hmo-authority-evidence">
      <div>
        <h4 className="text-sm font-medium">Authority enrichment</h4>
        <p className="text-xs muted">Only persisted, accepted claims are shown. Ambiguous candidates are intentionally withheld.</p>
      </div>
      {entries.length === 0 ? (
        <p className="text-sm muted">No Mazal, KIMA, VIAF, or Wikidata claims are attached.</p>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          {entries.map((entry) => (
            <div key={`${entry.kind}:${entry.property}:${entry.value}`} className="contents">
              <dt className="muted">{entry.kind}</dt>
              <dd className="font-mono text-xs break-all">
                {entry.href ? (
                  <a href={entry.href} target="_blank" rel="noreferrer" className="underline">
                    {entry.value} ↗
                  </a>
                ) : entry.value}
                <span className="ml-2 muted" title={entry.property}>({entry.property})</span>
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
