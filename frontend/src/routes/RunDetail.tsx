import {Navigate, useParams} from "react-router-dom";

/**
 * Compatibility route for bookmarks to the retired Authority editor.
 * Immediately replaces with HMO Wikibase Studio (Rule W-93 / W-102).
 */
export default function RunDetail() {
  const {runId} = useParams<{runId: string}>();
  const destination = runId ? `/runs/${runId}/hmo-studio` : "/";
  return <Navigate to={destination} replace />;
}
