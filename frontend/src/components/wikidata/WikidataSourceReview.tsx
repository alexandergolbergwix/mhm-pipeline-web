import {useEffect, useRef, useState} from "react";

import {SourceReview, type SourceCandidate, type SourceChoiceRequest, type SourceReviewRecord} from "@/api/wikidataSourceReview";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "The source review failed.";
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    identity_review: "Needs identity review", source_review: "Needs source review",
    unchecked_passage: "Unchecked source passages", unchecked_passages: "Unchecked source passages",
    reviewed_local: "Local choice; public check required", rejected_match: "Rejected match",
    documented_unresolved: "Documented unresolved fact", source_gap: "Source gap", stale_review: "Stale choices",
    requires_public_verification: "Public check required", documented_model_difference: "Different manuscript/work model",
    person_date_alignment_unproven: "The source does not prove which date belongs to each name.",
    person_role_alignment_unproven: "The source does not prove which role belongs to each name.",
  };
  return labels[status] ?? status.replaceAll("_", " ");
}

function CandidateForm({candidate, save, busy}: {
  candidate: SourceCandidate;
  save: (request: Omit<SourceChoiceRequest, "local_id">) => Promise<void>;
  busy: boolean;
}) {
  const [target, setTarget] = useState(candidate.choice?.target_qid ?? candidate.choice?.rejected_qid ?? "");
  const [evidence, setEvidence] = useState(candidate.choice?.evidence_uri ?? "");
  const [reason, setReason] = useState(candidate.choice?.reason ?? "");
  const passage = candidate.kind === "passage";
  const holding = candidate.kind === "holding";
  const base = {control_number: candidate.control_number, candidate_key: candidate.candidate_key,
    expected_source_hash: candidate.source_hash};
  const submit = (decision: "select" | "reject" | "document") => save({...base, decision,
    target_qid: target.trim() || undefined, evidence_uri: evidence.trim(), reason: reason.trim()});
  return <details className="border border-white/10 rounded p-2">
    <summary className="cursor-pointer text-sm" dir="auto">{candidate.title} · {candidate.source_key} · {statusLabel(candidate.status)}</summary>
    <div className="space-y-2 pt-2">
      <p className="text-sm whitespace-pre-wrap" dir="auto">{candidate.source_text}</p>
      {candidate.current_targets?.map(target => <p key={target} className="text-xs">Current draft target: {/^Q[1-9][0-9]*$/.test(target)
        ? <a href={`https://www.wikidata.org/wiki/${target}`} target="_blank" rel="noreferrer">{target}</a> : target}</p>)}
      {candidate.author && <p dir="auto">Author: {candidate.author}</p>}
      {candidate.folio && <p dir="auto">Source folio: {candidate.folio}</p>}
      {candidate.scope_text && <p dir="auto">Source scope: {candidate.scope_text}</p>}
      {candidate.scope_alignment_unproven && <p className="text-xs">The source scope needs review. Record the unresolved fact with its source passage.</p>}
      {candidate.shelfmark && <p dir="auto">Source shelfmark: {candidate.shelfmark}</p>}
      {candidate.year && <p>Publication year: {candidate.year}</p>}
      {candidate.pages && <p>Source pages: {candidate.pages}</p>}
      {!holding && !passage && <label className="block text-xs">Existing target QID
        <input className="input-glass w-full" value={target} onChange={event => setTarget(event.target.value)} placeholder="Q123" />
      </label>}
      <label className="block text-xs">Evidence URL
        <input className="input-glass w-full" type="url" value={evidence} onChange={event => setEvidence(event.target.value)} />
      </label>
      <label className="block text-xs">Reason or unresolved fact
        <textarea className="input-glass w-full" value={reason} onChange={event => setReason(event.target.value)} />
      </label>
      <div className="flex gap-2 flex-wrap">
        <button type="button" disabled={busy || candidate.scope_alignment_unproven || !evidence.trim() || !reason.trim() || (!passage && !holding && !/^Q[1-9][0-9]*$/.test(target.trim()))}
          onClick={() => void submit(passage ? "document" : "select")}>
          {passage ? "Record unresolved passage" : "Save local choice"}
        </button>
        {!holding && !passage && <button type="button" disabled={busy || !reason.trim() || !evidence.trim() || !/^Q[1-9][0-9]*$/.test(target.trim())}
          onClick={() => void submit("reject")}>Reject match</button>}
        {candidate.choice && <button type="button" disabled={busy} onClick={() => void save({...base, discard: true})}>Clear choice</button>}
      </div>
    </div>
  </details>;
}

export function WikidataSourceReview({runId, localId, onSaved}: {
  runId: string; localId: string; onSaved: () => void;
}) {
  return <SourceReviewScope key={JSON.stringify([runId, localId])} runId={runId} localId={localId} onSaved={onSaved} />;
}

function SourceReviewScope({runId, localId, onSaved}: {
  runId: string; localId: string; onSaved: () => void;
}) {
  const mounted = useRef(true);
  const [records, setRecords] = useState<SourceReviewRecord[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    mounted.current = true;
    return () => {mounted.current = false;};
  }, []);
  useEffect(() => {
    let active = true;
    setReady(false);
    setError("");
    void SourceReview.get(runId, localId).then(result => {
      if (active) {setRecords(result.records); setReady(true);}
    }).catch((failure: unknown) => {if (active) setError(errorText(failure));});
    return () => {active = false;};
  }, [runId, localId, refresh]);
  async function save(request: Omit<SourceChoiceRequest, "local_id">) {
    setBusy(true);
    setError("");
    try {
      await SourceReview.save(runId, {...request, local_id: localId});
      if (!mounted.current) return;
      setNotice("The choice is local. Rebuild the items, then review and approve the result.");
      setRefresh(value => value + 1);
      onSaved();
    } catch (failure: unknown) {
      if (mounted.current) setError(errorText(failure));
    } finally {if (mounted.current) setBusy(false);}
  }
  return <section className="space-y-3 border-t border-white/10 pt-3" aria-label="Gila source review">
    <h4 className="text-sm font-medium">Gila source review</h4>
    <p className="text-xs muted">The project keeps physical manuscripts and works in separate items. A local choice does not prove a public correction.</p>
    <p className="text-xs muted">A catalogue-status note describes the catalogue record. It does not describe a manuscript inscription.</p>
    {error && <p role="alert" className="text-danger text-sm">{error}</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    {!ready && !error && <p className="text-xs muted">The source review is not ready.</p>}
    {ready && records.map(record => <div key={record.control_number} className="space-y-2">
      <h5>Source record {record.control_number}</h5>
      {record.source_url && <a className="text-xs" href={record.source_url} target="_blank" rel="noreferrer">Open the catalogue source</a>}
      {!record.original_occurrences_available && <p className="text-xs">The record has flat MARC columns. Original field occurrences need a new source import.</p>}
      <table className="text-xs w-full"><thead><tr><th>Point</th><th>Requirement</th><th>State</th></tr></thead>
        <tbody>{record.requirements.map(requirement => <tr key={requirement.point}>
          <td>{requirement.point}</td><td>{requirement.title}</td><td>{statusLabel(requirement.status)}</td>
        </tr>)}</tbody>
      </table>
      <ul className="text-xs">{Object.entries(record.counts).map(([status, count]) => <li key={status}>{statusLabel(status)}: {count}</li>)}</ul>
      {record.source_gaps.map(gap => <p key={gap.field} className="text-xs">Source gap: {gap.field}. Email point {gap.point} needs more source evidence.</p>)}
      {record.record_status_notes.map(note => <p key={note} className="text-sm" dir="auto">Catalogue status: {note}</p>)}
      {record.source_extent_text && <p className="text-sm" dir="auto">Source extent: {record.source_extent_text}</p>}
      {record.person_subfield_reviews?.map((review, index) => <p key={index} className="text-xs">{review.source_field}: {statusLabel(review.reason)}</p>)}
      {record.catalog_references.map((citation, index) => <p key={index} className="text-sm" dir="auto">Bibliography ({citation.source_field}): {citation.original_citation}</p>)}
      {record.stale_choices.map(({choice, reason}) => <div key={choice.candidate.candidate_key}>
        <p className="text-danger text-xs">Stale choice: {reason}</p>
        <button type="button" disabled={busy} onClick={() => void save({control_number: record.control_number,
          candidate_key: choice.candidate.candidate_key, expected_source_hash: record.source_hash, discard: true})}>Clear stale choice</button>
      </div>)}
      {record.candidates.map(candidate => <CandidateForm key={`${candidate.candidate_key}-${refresh}`} candidate={candidate} save={save} busy={busy} />)}
    </div>)}
  </section>;
}
