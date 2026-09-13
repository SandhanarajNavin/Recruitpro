"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { User } from "@/lib/types";

const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/candidates", label: "Candidates" },
  { href: "/jobs", label: "Jobs" },
];

export function NavBar({ user, onSignOut }: { user: User | null; onSignOut: () => void }) {
  const pathname = usePathname();

  return (
    <header className="navbar">
      <div className="navbar-brand">
        <Link href="/dashboard" className="brand">
          Recruitment&nbsp;Agent
        </Link>
        <nav className="navlinks">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={pathname.startsWith(link.href) ? "navlink navlink-on" : "navlink"}
            >
              {link.label}
            </Link>
          ))}
        </nav>
      </div>
      <div className="navbar-user">
        {user ? <span className="navbar-name">{user.name}</span> : null}
        <button type="button" className="btn btn-ghost btn-sm" onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </header>
  );
}
