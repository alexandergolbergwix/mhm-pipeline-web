import {useEffect, useRef, useState} from "react";
import {Link, useParams} from "react-router-dom";

import {readRepairPlan, type RepairInventoryPage} from "@/api/wikidataRepairPlan";
import {Glass} from "@/components/glass";
import {Layout} from "@/components/Layout";

const steps = [
  ["Review the source", "Confirm each work, person, shelfmark, and source passage in Studio."],
  ["Correct the local draft", "Remove an invalid positional edit only after you confirm its current property and source."],
  ["Rebuild and verify", "Rebuild the canonical items. Run the quality checks and review each unresolved case."],
  ["Review the release", "Prepare a Publication release. Review its exact items and changes."],
  ["Run the dry run", "Confirm fresh creator proof and source evidence. Foreign items can serve as link targets only."],
  ["Publish the reviewed changes", "Use the normal Publication controls. Automatic repairs retain maxlag=5 and the saved retry time."],
  ["Verify the public result", "Read each corrected item again. Retain its revision and receipt. Report each unresolved case."],
];

export default function WikidataRepairPlan() {
  const {runId} = useParams<{runId: string}>();
  const [plan, setPlan] = useState<RepairInventoryPage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);

  async function load(cursor = "", buildId?: string) {
    if (!runId) return;
    const request = ++sequence.current;
    setBusy(true);
    setError("");
    setPlan(null);
    try {
      const result = await readRepairPlan(runId, cursor, buildId);
      if (request === sequence.current) setPlan(result);
    } catch (cause) {
      if (request === sequence.current) setError(cause instanceof Error ? cause.message : "The repair plan request failed.");
    } finally {
      if (request === sequence.current) setBusy(false);
    }
  }

  useEffect(() => {
    void load();
    return () => { ++sequence.current; };
  // The run owns the request scope. Ignore responses from the previous run.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  return <Layout>
    <div className="space-y-6">
      <Glass as="section" className="p-6 space-y-3">
        <h1 className="text-2xl font-semibold">Wikidata repair plan</h1>
        <p>This page reads the saved canonical build and the local edits. It does not run repairs or verify public items.</p>
        <p>Only Alexander Goldberg IL items can receive public edits. The normal write path must verify creator UID 6935067 before each write.</p>
        <div className="flex gap-3">
          <Link className="button-primary" to={`/runs/${runId}/wikidata-studio`}>Open Studio</Link>
          <button className="button-ghost" disabled={busy} onClick={() => void load()}>Refresh plan</button>
        </div>
        {busy && <p role="status">Please wait for the saved plan.</p>}
        {error && <p role="alert" className="text-danger">{error}</p>}
      </Glass>
      <Glass as="section" className="p-6 space-y-3">
        <h2 className="text-xl font-semibold">Repair steps</h2>
        <ol className="list-decimal pl-6 space-y-3">{steps.map(([title, description]) =>
          <li key={title}><strong>{title}.</strong> {description}</li>,
        )}</ol>
        <p>A local choice does not prove a public correction. An empty page does not prove that all Gila requirements pass.</p>
      </Glass>
      {plan && <Glass as="section" className="p-6 space-y-3">
        <h2 className="text-xl font-semibold">Saved item inventory</h2>
        <p>{plan.items.length} items on this page. {plan.total_items} items in this build.</p>
        <p>Build date: {plan.built_at}. Public verification: not checked.</p>
        <p>The codes come from the saved build. A QID in a positional edit requires review; the QID can be valid.</p>
        <div className="overflow-x-auto"><table className="w-full text-sm">
          <thead><tr><th className="text-left">Item</th><th className="text-left">Saved validation codes</th><th className="text-left">QID edit positions</th></tr></thead>
          <tbody>{plan.items.map(item => <tr key={item.local_id}>
            <td className="p-2"><div>{item.label}</div><code>{item.local_id}</code><div>{item.existing_qid}</div></td>
            <td className="p-2">{item.validation_codes.join(", ") || "No saved code"}</td>
            <td className="p-2">{item.qid_edit_indices.join(", ") || "None"}</td>
          </tr>)}</tbody>
        </table></div>
        {plan.next_cursor && <button className="button-ghost" disabled={busy}
          onClick={() => void load(plan.next_cursor ?? "", plan.build_token)}>Next page</button>}
      </Glass>}
    </div>
  </Layout>;
}
