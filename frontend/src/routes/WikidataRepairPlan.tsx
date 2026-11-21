import {useParams} from "react-router-dom";

import {Layout} from "@/components/Layout";
import {WikidataRepairInventory} from "@/components/wikidata/WikidataRepairInventory";

export default function WikidataRepairPlan() {
  const {runId} = useParams<{runId: string}>();
  return <Layout>{runId && <WikidataRepairInventory key={runId} runId={runId} />}</Layout>;
}
