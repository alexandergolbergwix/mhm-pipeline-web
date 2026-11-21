import {useEffect, useState, type ReactNode} from "react";
import {NavLink} from "react-router-dom";

import {Layout} from "@/components/Layout";
import {Admin} from "@/api/admin";
import {useAuth} from "@/stores/auth";
import {Glass, GlassPill} from "@/components/glass";

interface Props {
  children: ReactNode;
}

export function AdminLayout({children}: Props) {
  const administrator = useAuth(state => state.user?.role === "admin");
  const [pendingCount, setPendingCount] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function fetchStats() {
      try {
        const stats = await Admin.getStats();
        if (!cancelled) setPendingCount(stats.pending_access_requests);
      } catch {
        // silent — badge just won't show
      }
    }

    void fetchStats();
    const id = setInterval(() => { void fetchStats(); }, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <Layout>
      <div className="flex min-h-[60vh] flex-col gap-4 md:flex-row md:gap-6">
        <Glass as="aside" variant="compact" className="w-full shrink-0 md:w-44 md:self-start">
          <div className="kicker hidden px-3 py-2 md:block">Admin</div>
          <nav aria-label="Admin navigation" className="flex flex-wrap gap-1 md:flex-col">
            <SideNavLink to="/admin" end>Dashboard</SideNavLink>
            <SideNavLink to="/admin/access-requests">
              <span className="flex items-center justify-between gap-2 w-full">
                <span>Access Requests</span>
                {pendingCount > 0 && (
                  <GlassPill className="text-xs badge-warn px-1.5 py-0.5 min-w-[1.4rem] text-center" data-testid="admin-pending-badge">
                    {pendingCount}
                  </GlassPill>
                )}
              </span>
            </SideNavLink>
            {administrator && <SideNavLink to="/admin/wikidata-gila-repairs">Manuscript audit and repair</SideNavLink>}
            <SideNavLink to="/admin/users">Users</SideNavLink>
            <SideNavLink to="/admin/projects">Projects</SideNavLink>
            <SideNavLink to="/admin/invites">Invites</SideNavLink>
          </nav>
        </Glass>
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </Layout>
  );
}

interface SideNavLinkProps {
  to: string;
  end?: boolean;
  children: ReactNode;
}

function SideNavLink({to, end, children}: SideNavLinkProps) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({isActive}) =>
        [
          "flex items-center rounded-xl px-3 py-2 text-sm transition md:w-full",
          isActive ? "bg-[var(--nav-active-bg)] font-medium text-ink" : "muted hover:bg-[var(--surface-inset)] hover:text-ink",
        ].join(" ")
      }
    >
      {children}
    </NavLink>
  );
}
