import type {HmoCoverageEntry} from "@/api/hmoStudio";

const PROJECTION_STATUS_EXPLAIN: Record<HmoCoverageEntry["projection_status"], string> = {
  direct_wikidata_item:
    "Creates or links a public Wikidata item for this class when evidence is strong enough.",
  summarized_in_wikidata:
    "Stays as an HMO node; selected public facts are summarised onto manuscript, work, or person Wikidata claims.",
  hmo_or_wikibase_only:
    "Scholarly / structural modelling stays in HMO (or the project Wikibase). No default public Wikidata item.",
  unknown:
    "No projection strategy is registered for this class yet — treat as HMO-only until mapped.",
};

const PROPERTY_LABELS: Record<string, string> = {
  P17: "country",
  P19: "place of birth",
  P20: "place of death",
  P31: "instance of",
  P50: "author",
  P106: "occupation",
  P127: "owned by",
  P131: "located in",
  P136: "genre",
  P144: "based on",
  P186: "material used",
  P195: "collection",
  P214: "VIAF ID",
  P217: "inventory number",
  P248: "stated in",
  P282: "writing system",
  P407: "language of work or name",
  P478: "volume",
  P527: "has part(s)",
  P569: "date of birth",
  P570: "date of death",
  P571: "inception",
  P580: "start time",
  P582: "end time",
  P854: "reference URL",
  P887: "based on heuristic",
  P921: "main subject",
  P953: "full work available at URL",
  P958: "section, verse, paragraph, or clause",
  P973: "described at URL",
  P1001: "applies to jurisdiction",
  P1071: "location of creation",
  P1343: "described by source",
  P1476: "title",
  P1480: "sourcing circumstances",
  P1559: "name in native language",
  P1574: "exemplar of",
  P1684: "inscription",
  P1932: "object named as",
  P2635: "number of parts of this work",
  P3831: "object has role",
  P4969: "derivative work",
  P5008: "on focus list of Wikimedia project",
  P5816: "state of conservation",
  P6108: "IIIF manifest URL",
  P6216: "copyright status",
  P7153: "significant place",
  P7535: "scope and content",
  P8189: "National Library of Israel ID",
  P9302: "script style",
  P11603: "transcribed by",
  P1319: "earliest date",
  P1326: "latest date",
};

export function projectionStatusLabel(status: HmoCoverageEntry["projection_status"]): string {
  if (status === "direct_wikidata_item") return "direct";
  if (status === "summarized_in_wikidata") return "summarised";
  if (status === "hmo_or_wikibase_only") return "HMO-only";
  return "unknown";
}

export function projectionStatusExplanation(
  status: HmoCoverageEntry["projection_status"],
): string {
  return PROJECTION_STATUS_EXPLAIN[status] ?? PROJECTION_STATUS_EXPLAIN.unknown;
}

export function wikidataPropertyLabel(pid: string): string {
  return PROPERTY_LABELS[pid] ?? "Wikidata property";
}

export function wikidataPropertyHref(pid: string): string {
  return `https://www.wikidata.org/wiki/Property:${encodeURIComponent(pid)}`;
}

/** Compact native-tooltip text for hover. */
export function coverageRowHoverText(row: HmoCoverageEntry): string {
  const parts = [
    `${row.class_local_name} — ${projectionStatusLabel(row.projection_status)}`,
    projectionStatusExplanation(row.projection_status),
  ];
  const representation = row.wikidata_representation?.trim();
  if (representation) parts.push(`Wikidata: ${representation}`);
  const notes = row.notes?.trim();
  if (notes) parts.push(notes);
  if (row.wikidata_properties.length > 0) {
    parts.push(
      `Properties: ${row.wikidata_properties
        .map((pid) => {
          const label = PROPERTY_LABELS[pid];
          return label ? `${pid} (${label})` : pid;
        })
        .join(" · ")}`,
    );
  }
  parts.push("Click the row for the full explanation.");
  return parts.join("\n\n");
}
