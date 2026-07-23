import { Link } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { useAuth } from "@/stores/auth";
import {Glass} from "@/components/glass";

export default function Home() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  return (
    <Layout>
      <div className="space-y-6">
        <Glass as="section" className="p-8 space-y-3">
          <div className="kicker">Phase 1+2 — authentication, invites, password flows</div>
          <h2 className="text-2xl font-semibold">
            Hello {user?.name?.split(" ")[0] ?? "there"}.
          </h2>
          <p className="muted leading-relaxed max-w-2xl">
            You're signed in via a zero-knowledge session. Your encryption key
            lives wrapped in <code className="text-biu-sky">sessions.kek_wrapped</code>{" "}
            and only unlocks while your cookie is presented. Below are the
            upcoming surfaces — they ship as the next phases land.
          </p>

          <div className="flex flex-wrap gap-3 pt-2">
            {isAdmin && (
              <Link to="/admin/invites" className="button-primary">
                Invite a teammate
              </Link>
            )}
            <Link to="/settings" className="button-ghost">
              Account settings
            </Link>
          </div>
        </Glass>

        <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <RoadmapCard
            tag="Phase 3"
            title="Projects + memberships"
            body="Create projects, invite collaborators per project, and switch between them from the top nav."
          />
          <RoadmapCard
            tag="Phase 4–5"
            title="MARC upload + curator review"
            body="Drop in a MARC export, review extracted entities and canonical HMO results, and approve changes collaboratively."
          />
          <RoadmapCard
            tag="Phase 6"
            title="Git-styled history"
            body="Every approval / edit is an event in an append-only log. Tag named snapshots. Restore the project to any prior state."
          />
          <RoadmapCard
            tag="Phase 7"
            title="Real-time collaboration"
            body="Two curators on the same project see each other's approvals land instantly, with presence avatars."
          />
          <RoadmapCard
            tag="Phase 8"
            title="Liquid-glass surfaces"
            body="Three.js MeshTransmissionMaterial overlays on hero surfaces: real refraction, specular highlights, gentle ripple."
          />
          <RoadmapCard
            tag="Phase 9"
            title="Encrypted API keys"
            body="Per-user Gemini/Wikidata/Wikibase tokens — wrapped with your password-derived KEK, never readable on the server alone."
          />
        </section>
      </div>
    </Layout>
  );
}

function RoadmapCard({ tag, title, body }: { tag: string; title: string; body: string }) {
  return (
    <Glass as="article" className="p-6 space-y-2">
      <div className="kicker">{tag}</div>
      <h3 className="text-lg font-medium">{title}</h3>
      <p className="text-sm muted leading-relaxed">{body}</p>
    </Glass>
  );
}
