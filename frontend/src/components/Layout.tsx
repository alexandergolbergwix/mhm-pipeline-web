import { Link, NavLink, type NavLinkProps } from "react-router-dom";
import type { ReactNode } from "react";

import {Glass, GlassPill} from "@/components/glass";
import {useAuth} from "@/stores/auth";


interface Props {
  children: ReactNode;
}

export function Layout({children}: Props) {
  const {user, logout} = useAuth();
  return (
    <div className="min-h-screen p-6 md:p-10 max-w-6xl mx-auto space-y-6">
      <Glass as="header" className="px-6 py-4 flex flex-wrap items-center gap-4 justify-between">
        <Link to="/" className="flex flex-col">
          <span className="kicker">Bar-Ilan University · MHM</span>
          <span className="text-lg font-semibold">MHM Pipeline</span>
        </Link>

        <nav className="flex items-center gap-3 text-sm">
          <NavItem to="/">Projects</NavItem>
          {user?.role === "admin" && <NavItem to="/admin">Admin</NavItem>}
          <NavItem to="/settings">Settings</NavItem>
        </nav>

        <div className="flex items-center gap-3">
          <span className="text-sm muted hidden md:inline">
            {user?.name} <span className="opacity-60">· {user?.email}</span>
          </span>
          <GlassPill className="px-3 py-1 text-xs kicker">{user?.role}</GlassPill>
          <button onClick={() => logout()} className="button-ghost text-sm">
            Sign out
          </button>
        </div>
      </Glass>

      <main>{children}</main>
    </div>
  );
}

function NavItem(props: NavLinkProps) {
  return (
    <NavLink
      {...props}
      className={({isActive}) =>
        [
          "px-3 py-1.5 rounded-full transition",
          isActive ? "text-ink bg-white/10" : "muted hover:text-ink",
        ].join(" ")
      }
    />
  );
}
