import { Link, NavLink, type NavLinkProps } from "react-router-dom";
import type { ReactNode } from "react";

import {Glass, GlassPill} from "@/components/glass";
import {JobTrayBootstrap} from "@/components/jobs/JobTray";
import {ThemeToggle} from "@/components/ThemeToggle";
import {useAuth} from "@/stores/auth";


interface Props {
  children: ReactNode;
}

export function Layout({children}: Props) {
  const {user, logout} = useAuth();
  return (
    <div className="mx-auto min-h-screen max-w-6xl space-y-6 p-4 sm:p-6 md:p-10">
      <Glass as="header" contentClassName="flex flex-wrap items-center justify-between gap-3">
        <Link to="/" className="flex flex-col">
          <span className="kicker">Bar-Ilan University · MHM</span>
          <span className="text-lg font-semibold">MHM Pipeline</span>
        </Link>

        <nav className="flex flex-wrap items-center gap-1 text-sm sm:gap-3">
          <NavItem to="/">Projects</NavItem>
          {user?.role === "admin" && <NavItem to="/admin">Admin</NavItem>}
          <NavItem to="/settings">Settings</NavItem>
        </nav>

        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
          <ThemeToggle />
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
      <JobTrayBootstrap />
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
          isActive ? "text-ink bg-[var(--nav-active-bg)]" : "muted hover:text-ink",
        ].join(" ")
      }
    />
  );
}
