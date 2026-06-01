/**
 * MARC source viewer client.
 *
 * Thin typed wrapper for the new
 * ``/runs/{id}/extraction/marc-source/{control_number}`` endpoint.
 * Used by the right-side drawer in the Stage-2 review surface so the
 * curator can see the full MARC record an entity was extracted from
 * (with the entity's span highlighted) while the entity table is
 * still browsable in the background.
 *
 * The backend response carries the full MARC record dict shaped by
 * ``backend/app/pipeline/marc_ingest.py`` plus a per-entity span hint
 * list so the drawer can render multiple entity highlights at once
 * (yellow for the currently-selected entity, blue for others from
 * the same record).
 */

import { api } from "@/api/client";


export interface MarcSourceEntity {
  /** Stable content-addressed id (same key Stage-2 uses everywhere). */
  id:     string;
  text:   string;
  type:   string;
  role:   string;
  /** Character offsets into the MARC field where the entity was
   *  extracted from. ``null`` when the entity could not be located —
   *  the drawer falls back to substring search in that case. */
  start:  number | null;
  end:    number | null;
  /** The NER model that produced it (``person_ner`` /
   *  ``provenance_ner`` / ``contents_ner`` / ``genre_classifier``). */
  source: string;
}


export interface MarcSource {
  control_number: string;
  /** Full MARC record dict — same shape Stage 1 persists; the drawer
   *  renders it field-by-field. */
  marc:           Record<string, unknown>;
  entities:       MarcSourceEntity[];
}


export const MarcSourceApi = {
  get: (runId: string, controlNumber: string) =>
    api.get<MarcSource>(
      `/runs/${runId}/extraction/marc-source/${encodeURIComponent(controlNumber)}`,
    ),
};
