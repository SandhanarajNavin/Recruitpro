"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  IconBell,
  IconCandidates,
  IconClose,
  IconJobs,
  IconResumes,
  IconShortlist,
} from "@/components/Icons";
import { api } from "@/lib/api";
import type { ActivityItem } from "@/lib/types";

const ICONS: Record<string, (props: { size?: number }) => React.ReactElement> = {
  resume: IconResumes,
  job: IconJobs,
  screening: IconShortlist,
  application: IconCandidates,
};

/** Key for the last-seen marker. Per browser, which is what "seen" means here. */
const SEEN_KEY = "recruitpro.activity.seen";

/** Dismissed notifications, also per browser.
 *
 *  The feed is derived from audit events rather than stored per-recipient, so there
 *  is no row to delete and no endpoint to delete it with. Dismissal is therefore
 *  local, exactly like the seen marker above: it hides the item for this browser and
 *  does not touch the underlying activity.
 */
const DISMISSED_KEY = "recruitpro.activity.dismissed";

/** How far a swipe must travel before releasing it removes the row. */
const SWIPE_THRESHOLD = 72;

/** Where a dismissed row flies out to, and how long the flight lasts. */
const SWIPE_EXIT = 420;
const SWIPE_EXIT_MS = 170;

/** Identity for a feed item.
 *
 *  ActivityItem carries no id, so dismissal is keyed on the fields that together
 *  pin one event. Two genuinely identical events at the same instant would share a
 *  key, which is acceptable: they are indistinguishable to the reader too.
 */
function activityKey(item: ActivityItem): string {
  return `${item.kind}|${item.at}|${item.text}`;
}

function readDismissed(): string[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(DISMISSED_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((key): key is string => typeof key === "string") : [];
  } catch {
    // Unreadable or malformed: nothing is dismissed, so everything shows. Failing
    // towards showing the feed is the safe direction.
    return [];
  }
}

function writeDismissed(keys: string[]): void {
  try {
    // Bounded: the feed only ever returns recent activity, so older keys can never
    // match again and would otherwise grow without limit.
    window.localStorage.setItem(DISMISSED_KEY, JSON.stringify(keys.slice(-200)));
  } catch {
    // Dismissal then lasts for this session only, which is acceptable.
  }
}

function readSeen(): number {
  try {
    return Number(window.localStorage.getItem(SEEN_KEY) ?? 0);
  } catch {
    // Private mode and blocked site data both throw. An unreadable marker means
    // everything looks unseen, which is the safe direction to fail in.
    return 0;
  }
}

function writeSeen(at: number): void {
  try {
    window.localStorage.setItem(SEEN_KEY, String(at));
  } catch {
    // Nothing to do — the dot reappears next load, which is harmless.
  }
}

/** "2 hours ago" — relative time reads faster than a date in a feed. */
function since(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  const steps: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"],
    [60, "minute"],
    [24, "hour"],
    [7, "day"],
    [4.35, "week"],
    [12, "month"],
  ];
  let value = seconds;
  let unit: Intl.RelativeTimeFormatUnit = "second";
  for (const [size, next] of steps) {
    if (value < size) break;
    value /= size;
    unit = next;
  }
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(
    -Math.round(value),
    unit,
  );
}

/**
 * Activity feed in the top bar.
 *
 * The feed is fetched once when the shell mounts rather than on every route
 * change: it is a background awareness signal, not something the recruiter is
 * waiting on, and refetching it on each navigation would triple the request count
 * for no new information.
 */
export function NotificationBell() {
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [dismissed, setDismissed] = useState<string[]>([]);
  /** The row currently being dragged or flying out. */
  const [swipe, setSwipe] = useState<{ key: string; dx: number; animating: boolean } | null>(
    null,
  );
  const wrapRef = useRef<HTMLDivElement>(null);
  const startXRef = useRef(0);
  /** Set once a pointer has actually moved, so a drag does not follow the link. */
  const draggedRef = useRef(false);

  useEffect(() => {
    setSeen(readSeen());
    setDismissed(readDismissed());
    // A failed feed must never break the shell — the bell just stays empty.
    api.activity(8).then(setItems).catch(() => undefined);
  }, []);

  // Click-away and Escape both close it, the way a menu is expected to behave.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const visible = useMemo(
    () => items.filter((item) => !dismissed.includes(activityKey(item))),
    [items, dismissed],
  );
  const unread = visible.filter((item) => new Date(item.at).getTime() > seen).length;

  const remove = useCallback((key: string) => {
    setDismissed((current) => {
      if (current.includes(key)) return current;
      const next = [...current, key];
      writeDismissed(next);
      return next;
    });
    setSwipe(null);
  }, []);

  /** Slides the row out, then drops it once the animation has played. */
  const removeWithFlight = useCallback(
    (key: string, direction: number) => {
      setSwipe({ key, dx: direction * SWIPE_EXIT, animating: true });
      window.setTimeout(() => remove(key), SWIPE_EXIT_MS);
    },
    [remove],
  );

  /** Marks against the newest item rather than "now", so anything that lands
   *  while the panel is open still counts as unread afterwards. */
  const markAllRead = useCallback(() => {
    if (!visible.length) return;
    const newest = Math.max(...visible.map((item) => new Date(item.at).getTime()));
    writeSeen(newest);
    setSeen(newest);
  }, [visible]);

  /** "View all" widens the same feed rather than linking to a page that does not
   *  exist. Second fetch only; the first already covers the common case. */
  const showAll = useCallback(async () => {
    try {
      setItems(await api.activity(30));
      setExpanded(true);
    } catch {
      // Keep whatever is already on screen.
    }
  }, []);

  return (
    <div className="topbar-bell" ref={wrapRef}>
      <button
        type="button"
        className="icon-btn"
        onClick={() => setOpen((wasOpen) => !wasOpen)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={
          unread ? `Notifications, ${unread} unread` : "Notifications, none unread"
        }
      >
        <IconBell size={19} />
        {unread ? (
          <span className="bell-count" aria-hidden="true">
            {unread > 9 ? "9+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="bell-panel" role="dialog" aria-label="Recent activity">
          <div className="bell-head">
            <strong>Notifications</strong>
            {unread ? (
              <button type="button" className="bell-mark" onClick={markAllRead}>
                Mark all as read
              </button>
            ) : null}
          </div>
          {visible.length === 0 ? (
            <p className="bell-empty">
              {items.length ? "Nothing left here." : "Nothing has happened yet."}
            </p>
          ) : (
            <ul className="bell-list">
              {visible.map((item, index) => {
                const Icon = ICONS[item.kind] ?? IconResumes;
                const isUnread = new Date(item.at).getTime() > seen;
                const body = (
                  <>
                    <span
                      className={isUnread ? "bell-unread" : "bell-unread bell-read"}
                      aria-hidden="true"
                    />
                    <span className="activity-icon">
                      <Icon size={15} />
                    </span>
                    <span className="bell-text">
                      <span className="bell-title">{item.text}</span>
                      {item.context ? (
                        <span className="bell-context">{item.context}</span>
                      ) : null}
                    </span>
                    <span className="activity-time">{since(item.at)}</span>
                  </>
                );
                const key = activityKey(item);
                const offset = swipe?.key === key ? swipe.dx : 0;
                return (
                  <li
                    key={key}
                    className="bell-item"
                    style={{
                      transform: offset ? `translateX(${offset}px)` : undefined,
                      // Fades as it travels, so a swipe reads as removal rather
                      // than as sliding something into view.
                      opacity: offset ? Math.max(0, 1 - Math.abs(offset) / 220) : undefined,
                      transition:
                        swipe?.key === key && swipe.animating
                          ? `transform ${SWIPE_EXIT_MS}ms ease-out, opacity ${SWIPE_EXIT_MS}ms ease-out`
                          : "none",
                    }}
                    onPointerDown={(event) => {
                      // The remove button is not a swipe handle, and a right-click
                      // or middle-click must not drag the row either.
                      if (event.button !== 0) return;
                      if ((event.target as HTMLElement).closest(".bell-dismiss")) return;
                      startXRef.current = event.clientX;
                      draggedRef.current = false;
                      setSwipe({ key, dx: 0, animating: false });
                      event.currentTarget.setPointerCapture(event.pointerId);
                    }}
                    onPointerMove={(event) => {
                      if (swipe?.key !== key || swipe.animating) return;
                      const dx = event.clientX - startXRef.current;
                      // A few pixels of slop, so a plain click is never a drag.
                      if (Math.abs(dx) > 4) draggedRef.current = true;
                      setSwipe({ key, dx, animating: false });
                    }}
                    onPointerUp={(event) => {
                      if (swipe?.key !== key || swipe.animating) return;
                      const dx = event.clientX - startXRef.current;
                      // Either direction removes it, so the gesture works whichever
                      // way the hand goes.
                      if (Math.abs(dx) >= SWIPE_THRESHOLD) {
                        removeWithFlight(key, Math.sign(dx));
                      } else {
                        setSwipe({ key, dx: 0, animating: true });
                        window.setTimeout(
                          () => setSwipe((current) => (current?.key === key ? null : current)),
                          SWIPE_EXIT_MS,
                        );
                      }
                    }}
                    onPointerCancel={() => setSwipe(null)}
                  >
                    {item.href ? (
                      <Link
                        href={item.href}
                        className="bell-row"
                        onClick={(event) => {
                          // A swipe that ends over the link must not navigate.
                          if (draggedRef.current) {
                            event.preventDefault();
                            return;
                          }
                          setOpen(false);
                        }}
                      >
                        {body}
                      </Link>
                    ) : (
                      <span className="bell-row">{body}</span>
                    )}
                    <button
                      type="button"
                      className="bell-dismiss"
                      aria-label={`Remove notification: ${item.text}`}
                      onClick={() => removeWithFlight(key, -1)}
                    >
                      <IconClose size={15} />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {!expanded && items.length >= 8 ? (
            <button type="button" className="bell-foot" onClick={showAll}>
              View all notifications &rarr;
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
