"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  IconCandidates,
  IconChat,
  IconActivity,
  IconChevron,
  IconDashboard,
  IconJobs,
  IconLogo,
  IconLogout,
  IconMenu,
  IconSearch,
  IconResumes,
} from "@/components/Icons";
import { NotificationBell } from "@/components/NotificationBell";
import type { User } from "@/lib/types";

/**
 * Sidebar + top bar chrome for every signed-in page.
 *
 * Only sections that are actually wired to the API appear here. The design also
 * showed Screening, Reports, Settings and a billing panel; those have no backend
 * yet, and a nav item that leads nowhere is worse than an absent one.
 */

type NavItem = {
  href: string;
  label: string;
  Icon: (props: { size?: number; className?: string }) => React.ReactElement;
  /** Small badge after the label, e.g. "New". */
  badge?: string;
};

// Only routes that exist. Search Candidates, Shortlisted and Activity Log are in the
// design and have backing APIs (/search sessions, shortlisted results, audit_events)
// but no pages yet — listing them now would give a nav item that 404s.
// Design order, with the two items that have no page left out: "Screening" needs a
// run-history list (no endpoint yet) and "Settings" has no backend at all. A nav
// item that 404s is worse than an absent one.
const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", Icon: IconDashboard },
  { href: "/candidates", label: "Candidates", Icon: IconCandidates },
  { href: "/jobs", label: "Jobs", Icon: IconJobs },
  { href: "/resumes", label: "Resumes", Icon: IconResumes },
  { href: "/interviews", label: "Interviews", Icon: IconActivity },
  { href: "/assistant", label: "AI Assistant", Icon: IconChat, badge: "New" },
];

function initials(name: string | undefined): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/).slice(0, 2);
  return parts.map((part) => part[0]?.toUpperCase() ?? "").join("") || "?";
}

/** Page title from the path, so each route does not have to pass one down. */
function titleFor(pathname: string): string {
  const match = NAV.find(
    (item) => pathname === item.href || pathname.startsWith(`${item.href}/`),
  );
  if (match) return match.label;
  if (pathname.startsWith("/screenings")) return "Screening";
  return "Recruitment";
}

export function AppShell({
  user,
  onSignOut,
  children,
}: {
  user: User | null;
  onSignOut: () => void;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [navOpen, setNavOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  // Route change closes both overlays; leaving them open across a navigation
  // strands the drawer over the new page on mobile.
  useEffect(() => {
    setNavOpen(false);
    setMenuOpen(false);
  }, [pathname]);

  return (
    <div className={navOpen ? "app-shell app-shell-nav-open" : "app-shell"}>
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="sidebar-logo">
            <IconLogo size={26} />
          </span>
          <span className="sidebar-brand-text">
            <strong>RecruitPro</strong>
            <small>Hire Smarter</small>
          </span>
        </div>

        {user ? (
          <div className="sidebar-account" title={user.email}>
            <span className="sidebar-account-label">Signed in</span>
            <span className="sidebar-account-name">{user.name}</span>
          </div>
        ) : null}

        <nav className="sidebar-nav" aria-label="Main">
          {NAV.map(({ href, label, Icon, badge }) => {
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={href}
                href={href}
                className={active ? "sidenav-item sidenav-item-on" : "sidenav-item"}
                aria-current={active ? "page" : undefined}
              >
                <Icon size={19} />
                <span>{label}</span>
                {badge ? <span className="sidenav-badge">{badge}</span> : null}
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-foot">
          <div className="sidebar-help">
            <span className="sidebar-help-title">Need help?</span>
            <span className="sidebar-help-body">Get support or share feedback</span>
          </div>
          <button type="button" className="sidebar-logout" onClick={onSignOut}>
            <IconLogout size={19} />
            <span>Logout</span>
          </button>
        </div>
      </aside>

      {/* Tap-away target for the mobile drawer. */}
      <button
        type="button"
        className="sidebar-scrim"
        aria-label="Close navigation"
        onClick={() => setNavOpen(false)}
      />

      <div className="app-main">
        <header className="topbar">
          <button
            type="button"
            className="icon-btn topbar-burger"
            onClick={() => setNavOpen((open) => !open)}
            aria-label="Toggle navigation"
          >
            <IconMenu />
          </button>
          <h1 className="topbar-title">{titleFor(pathname)}</h1>

          <div className="topbar-search">
            <span className="search-icon">
              <IconSearch size={16} />
            </span>
            <input
              type="search"
              placeholder="Search candidates, jobs…"
              aria-label="Search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && query.trim()) {
                  router.push(`/candidates?q=${encodeURIComponent(query.trim())}`);
                }
              }}
            />
          </div>

          <div className="topbar-right">
            <NotificationBell />

            <div className="topbar-user">
              <button
                type="button"
                className="user-chip"
                onClick={() => setMenuOpen((open) => !open)}
                aria-expanded={menuOpen}
                aria-haspopup="menu"
              >
                <span className="avatar">{initials(user?.name)}</span>
                <span className="user-chip-text">
                  <strong>{user?.name ?? "Signed in"}</strong>
                  <small>{user?.role ?? ""}</small>
                </span>
                <IconChevron size={16} />
              </button>
              {menuOpen ? (
                <div className="user-menu" role="menu">
                  <span className="user-menu-email">{user?.email}</span>
                  <button type="button" role="menuitem" onClick={onSignOut}>
                    Sign out
                  </button>
                </div>
              ) : null}
            </div>
          </div>
        </header>

        <main className="app-content">{children}</main>
      </div>
    </div>
  );
}
