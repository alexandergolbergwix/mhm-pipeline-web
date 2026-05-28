import { useAuth } from "@/stores/auth";

export default function Home() {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen p-8 space-y-6 max-w-3xl mx-auto">
      <header className="flex items-center justify-between glass px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">MHM Pipeline</h1>
          <p className="text-sm text-glass-inkSub">
            Signed in as <span className="text-glass-ink">{user?.name}</span> ·{" "}
            {user?.email}
          </p>
        </div>
        <button onClick={() => logout()} className="button-primary">
          Sign out
        </button>
      </header>

      <section className="glass p-6 space-y-3">
        <h2 className="text-lg font-medium">Phase 1 — Authentication ✓</h2>
        <p className="text-glass-inkSub text-sm leading-relaxed">
          You're signed in via a zero-knowledge session. Your KEK lives wrapped
          in <code>sessions.kek_wrapped</code> and only unlocks while your
          cookie is presented. Next phases will add projects, the pipeline,
          real-time collaboration, version history, and the Three.js liquid-glass
          overlay.
        </p>
      </section>

      <section className="glass p-6 space-y-2">
        <h2 className="text-lg font-medium text-glass-inkSub">Coming next</h2>
        <ul className="text-sm text-glass-inkSub list-disc list-inside space-y-1">
          <li>Phase 2 — invites + password change/reset</li>
          <li>Phase 3 — projects + memberships</li>
          <li>Phase 4 — MARC upload + authority resolution</li>
          <li>Phase 5 — Authority Review UI with approvals</li>
          <li>Phase 6 — Git-styled history (event sourcing + restore)</li>
          <li>Phase 7 — Real-time collaboration</li>
          <li>Phase 8 — Three.js liquid-glass hero surfaces</li>
        </ul>
      </section>
    </div>
  );
}
