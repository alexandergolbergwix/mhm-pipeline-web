import {useEffect, useState, type ReactNode} from "react";
import {NavLink} from "react-router-dom";

import {Layout} from "@/components/Layout";
import {Admin} from "@/api/admin";

interface Props {
  children: ReactNode;
}

export function AdminLayout({children}: Props) {
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
      <div className="flex gap-6 min-h-[60vh]">
        <aside className="glass p-4 rounded-2xl flex-shrink-0 w-44 space-y-1 self-start">
          <div className="kicker px-2 pb-2">Admin</div>
          <SideNavLink to="/admin" end>Dashboard</SideNavLink>
          <SideNavLink to="/admin/access-requests">
            <span className="flex items-center justify-between gap-2 w-full">
              <span>Access Requests</span>
              {pendingCount > 0 && (
                <span
                  data-testid="admin-pending-badge"
                  className="glass-pill text-xs border-yellow-400/60 text-yellow-300 bg-yellow-500/10 px-1.5 py-0.5 min-w-[1.4rem] text-center"
                >
                  {pendingCount}
                </span>
              )}
            </span>
          </SideNavLink>
          <SideNavLink to="/admin/users">Users</SideNavLink>
          <SideNavLink to="/admin/projects">Projects</SideNavLink>
          <SideNavLink to="/admin/invites">Invites</SideNavLink>
        </aside>
        <main className="flex-1 min-w-0">{children}</main>
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
          "flex items-center px-3 py-2 rounded-xl text-sm transition w-full",
          isActive ? "bg-white/10 text-ink" : "muted hover:text-ink hover:bg-white/5",
        ].join(" ")
      }
    >
      {children}
    </NavLink>
  );
}
