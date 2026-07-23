import { Navigate, useParams } from "react-router-dom";

/**
 * Compatibility route for bookmarks to the retired Authority editor.
 * Authority enrichment now runs as part of HMO Wikibase Studio creation.
 */
export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  return <Navigate to={runId ? `/runs/${runId}/hmo-studio` : "/"} replace />;
}
