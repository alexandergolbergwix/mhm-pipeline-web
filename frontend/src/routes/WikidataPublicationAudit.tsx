import {Link, useParams} from "react-router-dom";

import {Glass} from "@/components/glass";
import {Layout} from "@/components/Layout";
import {WikidataPublicationAudit as PublicationAudit} from "@/components/wikidata/WikidataPublicationAudit";

export default function WikidataPublicationAudit() {
  const {runId, publicationId} = useParams<{runId: string; publicationId: string}>();

  if (!runId || !publicationId) {
    return <Layout><Glass className="p-6 text-danger">The Publication audit URL is incomplete.</Glass></Layout>;
  }

  return (
    <Layout>
      <div className="space-y-4">
        <Link className="text-sm text-biu-sky hover:underline" to={`/runs/${runId}/wikidata-studio`}>
          Back to Wikidata Studio
        </Link>
        <Glass className="p-6">
          <PublicationAudit runId={runId} publicationId={publicationId} />
        </Glass>
      </div>
    </Layout>
  );
}
