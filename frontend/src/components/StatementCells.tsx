/**
 * Shared, label-aware renderers for Wikidata snaks.
 *
 * Both the Studio's "Item view" (one item at a time, ItemPanel) and
 * the "Table view" (every statement across every item) flow through
 * the same two cells:
 *
 *   <PropertyPill p="P31" label="instance of" store={…} />
 *     → "instance of (P31)" — link to wikidata.org Property page.
 *
 *   <ValueRendering snak={s} store={…} />
 *     → "Ktiv (Q118384267)" — link to wikidata.org Q page.
 *     handles strings, dates, somevalue, novalue, URLs, NLI manuscript
 *     ids (special-cased to open the in-app MARC popup), etc.
 *
 * Label resolution waterfall:
 *   1. snak.property_label / snak.value_label — backend stamps these
 *      from desktop's static dictionary when known.
 *   2. labelStore.label(id) — frontend lazy-fetches the long tail via
 *      /api/wikidata/labels (live wbgetentities, batched + cached).
 *   3. Bare P/Q id — last-resort, monospace.
 */

import type { Snak } from "@/api/wikidataStudio";
import type { useLabelStore } from "@/api/wikidataLabels";


export type LabelStore = ReturnType<typeof useLabelStore>;


export function PropertyPill({
  p, label, store,
}: { p?: string; label?: string; store: LabelStore }) {
  if (!p) return <span className="muted">P?</span>;
  const text = label ?? store.label(p);
  return (
    <a href={`https://www.wikidata.org/wiki/Property:${p}`}
       target="_blank" rel="noreferrer"
       className="text-biu-sky text-xs hover:underline whitespace-nowrap">
      {text
        ? <>{text} <span className="font-mono muted">({p})</span></>
        : <span className="font-mono">{p}</span>}
    </a>
  );
}


export function ValueRendering({
  snak, store, small, onOpenMarc,
}: {
  snak: Snak;
  store: LabelStore;
  small?: boolean;
  /** Called when the value is an NLI manuscript identifier (P8189-style)
   *  — typically opens the in-app MARC record popup. Optional. */
  onOpenMarc?: (cn: string) => void;
}) {
  const cls = small ? "text-[11px]" : "text-xs";
  const kind = snak.value_type;
  if (kind === "somevalue") return <span className={`${cls} muted`}>(somevalue)</span>;
  if (kind === "novalue")   return <span className={`${cls} muted`}>(novalue)</span>;

  const prop = snak.property ?? snak.property_id;
  const vid  = snak.value_id;
  const val  = snak.value;

  // Special case: NLI catalog id — open the in-app MARC popup, not a
  // wikidata page (there isn't one for the bibliographic record).
  if (onOpenMarc && prop === "P8189" && typeof val === "string") {
    return (
      <button onClick={() => onOpenMarc(String(val))}
              className={`${cls} text-biu-sky hover:underline`}>
        <span className="font-mono">{String(val)}</span> ↗
      </button>
    );
  }

  // value_id is the typed slot for Q-ids in the dataclass.
  if (vid && /^Q\d+$/.test(vid)) {
    const text = snak.value_label ?? store.label(vid);
    return <QLink id={vid} text={text} cls={cls} />;
  }
  if (vid) return <span className={`${cls} font-mono`}>{vid}</span>;

  if (typeof val === "string") {
    // The desktop converter sometimes emits a Q-id as a value string.
    if (/^Q\d+$/.test(val)) {
      const text = snak.value_label ?? store.label(val);
      return <QLink id={val} text={text} cls={cls} />;
    }
    if (/^https?:\/\//.test(val)) {
      return (
        <a href={val} target="_blank" rel="noreferrer"
           className={`${cls} text-biu-sky font-mono hover:underline truncate inline-block max-w-md`}>
          {val}
        </a>
      );
    }
    return <span className={`${cls} font-mono`}>{val}</span>;
  }
  if (val == null) return <span className={`${cls} muted`}>—</span>;
  return <span className={`${cls} font-mono`}>{JSON.stringify(val)}</span>;
}


function QLink({ id, text, cls }: { id: string; text?: string; cls: string }) {
  return (
    <a href={`https://www.wikidata.org/wiki/${id}`}
       target="_blank" rel="noreferrer"
       className={`${cls} text-biu-sky hover:underline whitespace-nowrap`}>
      {text
        ? <>{text} <span className="font-mono muted">({id})</span></>
        : <span className="font-mono">{id}</span>}
    </a>
  );
}


/** Walk a Snak (and its qualifiers + references, recursively) and
 *  collect every P/Q identifier appearing anywhere in it. The Studio
 *  routes the resulting array through labelStore.resolve() so a single
 *  batched request fetches every missing label at once. */
export function collectIds(s: Snak | undefined, out: string[] = []): string[] {
  if (!s) return out;
  out.push(s.property ?? s.property_id ?? "");
  out.push(s.value_id ?? "");
  if (typeof s.value === "string") out.push(s.value);
  (s.qualifiers ?? []).forEach((q) => collectIds(q, out));
  (s.references ?? []).forEach((r) => {
    const snaks = Array.isArray((r as { snaks?: unknown }).snaks)
      ? ((r as { snaks: Snak[] }).snaks)
      : [r as Snak];
    snaks.forEach((sn) => collectIds(sn, out));
  });
  return out;
}


// S-properties from desktop's QuickStatements exporter — kept here so
// both ItemPanel's ReferenceRow and the table-view reference list use
// the same friendly names.
export const FRIENDLY_S_PROP: Record<string, string> = {
  P248:  "stated in",
  P854:  "URL",
  P813:  "retrieved",
  P143:  "imported from",
  P3452: "inferred from",
  P887:  "based on heuristic",
};
