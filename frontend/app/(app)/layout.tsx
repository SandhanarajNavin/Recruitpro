"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/AppShell";
import { clearSession, getStoredUser, getToken } from "@/lib/api";
import type { User } from "@/lib/types";

/**
 * Auth gate for every signed-in route.
 *
 * The check is client-side because the token lives in localStorage; it guards the
 * UI, not the data. Every endpoint re-authorises on the server, so a bypassed gate
 * shows an empty shell rather than someone else's candidates.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setUser(getStoredUser());
    setChecked(true);
  }, [router]);

  if (!checked) {
    return <div className="empty-state">Checking your session…</div>;
  }

  return (
    <AppShell
      user={user}
      onSignOut={() => {
        clearSession();
        router.replace("/login");
      }}
    >
      {children}
    </AppShell>
  );
}
