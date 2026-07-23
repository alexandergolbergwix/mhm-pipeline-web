import { Link, useNavigate, useParams } from "react-router-dom";
import { useEffect } from "react";
import { Glass } from "@/components/glass";

/**
 * Compatibility route for bookmarks to the retired Authority editor.
 * Authority enrichment now runs as part of HMO Wikibase Studio creation.
 */
export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const destination = runId ? `/runs/${runId}/hmo-studio` : "/";

  useEffect(() => {
    const timeout = window.setTimeout(() => navigate(destination, { replace: true }), 1200);
    return () => window.clearTimeout(timeout);
  }, [destination, navigate]);

  return (
    <main className="min-h-screen grid place-items-center p-6">
      <Glass as="section" className="max-w-lg p-8 space-y-4">
        <div className="kicker">Authority editor retired</div>
        <h1 className="text-2xl font-semibold">This review surface has moved</h1>
        <p className="muted leading-relaxed">
          Authority enrichment now runs inside HMO Wikibase Studio. Your
          bookmark will continue to work and will open the canonical review
          surface shortly.
        </p>
        <Link to={destination} replace className="button-primary inline-block">
          Open HMO Wikibase Studio
        </Link>
      </Glass>
    </main>
  );
}
