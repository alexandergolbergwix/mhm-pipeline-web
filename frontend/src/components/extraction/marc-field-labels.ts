/**
 * Friendly labels for the keys our backend exposes on the MARC record
 * dict (one entry per key in ``backend/app/pipeline/marc_ingest.py``).
 *
 * Two halves:
 *  - ``LABELS[key]`` → ``{ label, tag, weight, category }``
 *    where ``tag`` is the canonical MARC field number (245, 100, 561…)
 *    so the curator can grep their MARC printouts.
 *  - ``ENTITY_EXPECTED_FIELDS[entityType]`` → list of keys that the
 *    NER's classifier *should* have grounded against. Used to render
 *    the "expected vs found" comparison in the detail drawer.
 *
 * Weight controls render order (lower = first in the layout). Category
 * lets the drawer group keys by family ("identity" / "subject" /
 * "provenance" / "physical" / "other").
 */

export interface MarcFieldLabel {
  label:    string;
  tag:      string;
  weight:   number;
  category: "identity" | "title" | "subject" | "provenance"
          | "physical" | "dates" | "language" | "other";
}

// Render order: title block → contributors → subjects → places →
// provenance → physical → dates → notes → everything else.
export const MARC_FIELD_LABELS: Record<string, MarcFieldLabel> = {
  // Title block
  title:              { label: "Title",              tag: "245 ‡a", weight: 1,  category: "title" },
  subtitle:           { label: "Subtitle",           tag: "245 ‡b", weight: 2,  category: "title" },
  variant_titles:     { label: "Variant titles",     tag: "246",    weight: 3,  category: "title" },
  uniform_title:      { label: "Uniform title",      tag: "240",    weight: 4,  category: "title" },
  alternate_titles:   { label: "Alternate titles",   tag: "247",    weight: 5,  category: "title" },

  // Identity / responsibility
  contributors:       { label: "Contributors",       tag: "100/700", weight: 10, category: "identity" },
  authors:            { label: "Authors",            tag: "100",     weight: 11, category: "identity" },

  // Subjects
  subjects:           { label: "Subjects",           tag: "600/650", weight: 20, category: "subject" },
  genre_form:         { label: "Genre/form",         tag: "655",     weight: 21, category: "subject" },
  genres:             { label: "Genres",             tag: "655",     weight: 22, category: "subject" },

  // Provenance & ownership
  provenance:         { label: "Provenance",         tag: "561 ‡a",  weight: 30, category: "provenance" },
  former_owners:      { label: "Former owners",      tag: "700 ‡e",  weight: 31, category: "provenance" },
  ownership_history:  { label: "Ownership history",  tag: "561",     weight: 32, category: "provenance" },
  acquisition_source: { label: "Acquisition",        tag: "541",     weight: 33, category: "provenance" },

  // Places
  places:             { label: "Places",             tag: "651",     weight: 40, category: "subject" },
  related_places:     { label: "Related places",     tag: "752",     weight: 41, category: "subject" },

  // Series & contents
  series:             { label: "Series",             tag: "490/830", weight: 50, category: "other" },
  contents:           { label: "Contents",           tag: "505",     weight: 51, category: "other" },
  works:              { label: "Works",              tag: "505",     weight: 52, category: "other" },

  // Notes (free text)
  notes:              { label: "Notes",              tag: "500",     weight: 60, category: "other" },
  colophon_text:      { label: "Colophon",           tag: "500",     weight: 61, category: "other" },
  scribal_interventions: { label: "Scribal interventions", tag: "500", weight: 62, category: "other" },

  // Physical
  shelfmark:          { label: "Shelfmark",          tag: "852",     weight: 70, category: "physical" },
  holding_institution:{ label: "Holding institution", tag: "852 ‡a", weight: 71, category: "physical" },
  materials:          { label: "Materials",          tag: "340",     weight: 72, category: "physical" },
  height_mm:          { label: "Height (mm)",        tag: "300 ‡c",  weight: 73, category: "physical" },
  width_mm:           { label: "Width (mm)",         tag: "300 ‡c",  weight: 74, category: "physical" },
  script_type:        { label: "Script type",        tag: "500",     weight: 75, category: "physical" },
  script_mode:        { label: "Script mode",        tag: "500",     weight: 76, category: "physical" },

  // Dates
  dates:              { label: "Dates",              tag: "260/008", weight: 80, category: "dates" },
  date:               { label: "Date",               tag: "260 ‡c",  weight: 81, category: "dates" },

  // Languages
  languages:          { label: "Languages",          tag: "041",     weight: 90, category: "language" },

  // Rights
  rights_statement:   { label: "Rights",             tag: "540",     weight: 95, category: "other" },
  iiif_manifest_url:  { label: "IIIF manifest",      tag: "856",     weight: 96, category: "other" },
};


/** Which MARC fields an NER candidate's TYPE should map onto. The
 *  detail drawer uses this to show "Expected MARC fields" vs the
 *  fields where the candidate actually matched. Mirrors
 *  ``backend/app/pipeline/marc_structured_index._TYPE_TO_FIELDS``. */
export const ENTITY_EXPECTED_FIELDS: Record<string, string[]> = {
  PERSON:        ["contributors", "authors"],
  OWNER:         ["former_owners", "ownership_history", "acquisition_source", "provenance"],
  WORK:          ["title", "title_variants", "uniform_title", "alternate_titles", "contents", "works"],
  WORK_AUTHOR:   ["contributors", "authors"],
  GENRE:         ["genre_form", "genres"],
  PLACE:         ["places", "related_places", "subjects"],
  COLLECTION:    ["former_owners", "ownership_history", "acquisition_source"],
  DATE:          ["dates", "date"],
  FOLIO:         ["contents", "works"],
  ORG:           ["contributors", "former_owners"],
  OTHER:         [],
};


/** Translate a MARC value to a printable string (handles strings,
 *  arrays of strings or dicts, plain dicts).
 *  Hebrew text is preserved verbatim. */
export function stringifyMarcValue(value: unknown, maxItems = 6): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    const head = value.slice(0, maxItems).map((v) => stringifyMarcValue(v, maxItems)).filter(Boolean);
    const more = value.length > maxItems ? ` (+${value.length - maxItems} more)` : "";
    return head.join(" · ") + more;
  }
  if (typeof value === "object") {
    const rec = value as Record<string, unknown>;
    if (typeof rec.name === "string") {
      const extras: string[] = [];
      if (rec.role) extras.push(String(rec.role));
      if (rec.type) extras.push(String(rec.type));
      return extras.length > 0 ? `${rec.name} (${extras.join(", ")})` : String(rec.name);
    }
    if (typeof rec.term === "string") return String(rec.term);
    if (typeof rec.text === "string") return String(rec.text);
    if (typeof rec.value === "string") return String(rec.value);
    try { return JSON.stringify(rec); } catch { return ""; }
  }
  return "";
}


/** Return MARC keys present in a record, sorted by their label
 *  weight (matches the registry's natural render order). Unknown
 *  keys land at the end. */
export function orderedMarcKeys(marc: Record<string, unknown>): string[] {
  const known = new Set(Object.keys(MARC_FIELD_LABELS));
  const inRec = Object.keys(marc).filter((k) => marc[k] !== null && marc[k] !== undefined && marc[k] !== "" && (!Array.isArray(marc[k]) || (marc[k] as unknown[]).length > 0));
  return inRec.sort((a, b) => {
    const wa = MARC_FIELD_LABELS[a]?.weight ?? 999 + (known.has(a) ? 0 : 1);
    const wb = MARC_FIELD_LABELS[b]?.weight ?? 999 + (known.has(b) ? 0 : 1);
    return wa - wb;
  });
}
