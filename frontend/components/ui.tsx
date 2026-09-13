"use client";

import Link from "next/link";
import { Input as AntInput, Tabs as AntTabs } from "antd";
import { IconChevron, IconSearch } from "@/components/Icons";

/**
 * Shared vocabulary for the list and detail screens.
 *
 * One definition per element rather than nine near-copies: the design system uses
 * the same tiles, tabs, tables, pills and empty states on every page, and a table
 * that drifts between Candidates and Jobs reads as two products.
 */

/* ── status pills ─────────────────────────────────────────────────── */

/**
 * Every status the app can show, mapped to a tone. Kept in one place because the
 * same word means the same thing everywhere: "ready" is green on the Resumes table
 * and green on a candidate row.
 */
const TONES: Record<string, string> = {
  // screening verdicts
  strong_hire: "good",
  interview: "info",
  maybe: "warn",
  pass: "mute",
  // pipeline stages
  applied: "mute",
  screening: "info",
  shortlisted: "good",
  offer: "info",
  hired: "good",
  rejected: "bad",
  // resume ingestion
  ready: "good",
  queued: "mute",
  extracting: "warn",
  parsing: "warn",
  embedding: "warn",
  failed: "bad",
  // jobs
  open: "good",
  on_hold: "warn",
  closed: "mute",
  // derived job attention states — what the recruiter should do next
  needs_review: "warn",
  needs_candidates: "bad",
  // interview outcomes
  scheduled: "info",
  completed: "good",
  cancelled: "mute",
  no_show: "bad",
};

const LABELS: Record<string, string> = {
  strong_hire: "Strong hire",
  on_hold: "On hold",
  no_show: "No show",
};

export function humanise(value: string): string {
  if (LABELS[value]) return LABELS[value];
  return value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, " ");
}

export function StatusPill({ status }: { status: string }) {
  const tone = TONES[status] ?? "mute";
  return <span className={`pill pill-${tone}`}>{humanise(status)}</span>;
}

/* ── stat tiles ───────────────────────────────────────────────────── */

export type Tile = {
  key: string;
  label: string;
  value: number | string;
  hint?: string;
  Icon?: (props: { size?: number }) => React.ReactElement;
};

export function StatTiles({ tiles }: { tiles: Tile[] }) {
  if (tiles.length === 0) return null;
  return (
    <section className="stat-row">
      {tiles.map((tile) => (
        <article className="stat-tile" key={tile.key}>
          {tile.Icon ? (
            <span className="stat-tile-icon">
              <tile.Icon size={22} />
            </span>
          ) : null}
          <div className="stat-tile-body">
            <span className="stat-tile-label">{tile.label}</span>
            <span className="stat-tile-value">
              {typeof tile.value === "number" ? tile.value.toLocaleString() : tile.value}
            </span>
            {tile.hint ? <span className="stat-tile-delta delta-none">{tile.hint}</span> : null}
          </div>
        </article>
      ))}
    </section>
  );
}

/* ── tabs ─────────────────────────────────────────────────────────── */

export type Tab = { key: string; label: string; count?: number };

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: Tab[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <AntTabs
      activeKey={active}
      onChange={onChange}
      // The card already draws its own separator under the header.
      className="ui-tabs"
      items={tabs.map((tab) => ({
        key: tab.key,
        label: (
          <span className="tab-label">
            {tab.label}
            {tab.count === undefined ? null : (
              <span className="tab-count">{tab.count}</span>
            )}
          </span>
        ),
      }))}
    />
  );
}

/* ── page header ──────────────────────────────────────────────────── */

export function PageHead({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="page-head">
      <div>
        <h2>{title}</h2>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

/* ── search field ─────────────────────────────────────────────────── */

export function SearchField({
  value,
  onChange,
  placeholder = "Search…",
  onSubmit,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  onSubmit?: () => void;
}) {
  return (
    <AntInput
      className="ui-search"
      value={value}
      placeholder={placeholder}
      allowClear
      prefix={<IconSearch size={16} />}
      onChange={(event) => onChange(event.target.value)}
      onPressEnter={onSubmit}
    />
  );
}

/* ── empty state ──────────────────────────────────────────────────── */

/**
 * Screen 9 in the design. An empty state that only says "nothing here" wastes the
 * one moment the user is definitely looking for guidance, so suggestions are part
 * of the component rather than optional decoration.
 */
export function EmptyState({
  title,
  body,
  suggestions,
  action,
  Icon,
}: {
  title: string;
  body?: string;
  suggestions?: string[];
  action?: React.ReactNode;
  Icon?: (props: { size?: number }) => React.ReactElement;
}) {
  return (
    <div className="empty">
      {Icon ? (
        <span className="empty-icon">
          <Icon size={30} />
        </span>
      ) : null}
      <h3>{title}</h3>
      {body ? <p>{body}</p> : null}
      {action ? <div className="empty-action">{action}</div> : null}
      {suggestions?.length ? (
        <div className="empty-tips">
          <span>Suggestions</span>
          <ul>
            {suggestions.map((tip) => (
              <li key={tip}>{tip}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/* ── table ────────────────────────────────────────────────────────── */

export type Column<T> = {
  key: string;
  header: string;
  /** Right-aligned tabular numerals — use for counts and scores. */
  numeric?: boolean;
  render: (row: T) => React.ReactNode;
};

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  href,
  empty,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** When given, each row gets a chevron linking here. */
  href?: (row: T) => string;
  empty?: React.ReactNode;
}) {
  if (rows.length === 0) {
    return <>{empty ?? <p className="dash-empty">Nothing to show yet.</p>}</>;
  }
  return (
    <div className="table-scroll">
      <table className="listing">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.numeric ? "num" : undefined}>
                {column.header}
              </th>
            ))}
            {href ? <th aria-label="Open" /> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((column) => (
                <td key={column.key} className={column.numeric ? "num" : undefined}>
                  {column.render(row)}
                </td>
              ))}
              {href ? (
                <td className="chev">
                  <Link href={href(row)} aria-label="Open">
                    <IconChevron size={16} className="chev-right" />
                  </Link>
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── pipeline strip ───────────────────────────────────────────────── */

export const PIPELINE_ORDER = [
  "applied",
  "screening",
  "shortlisted",
  "interview",
  "offer",
  "hired",
] as const;

/**
 * The hiring funnel. Every stage is drawn even at zero — a funnel with gaps where
 * nobody stands reads as missing data rather than as an empty stage.
 */
/**
 * The hiring pipeline as a connected rail: one dot per stage on a single track,
 * label above, count below. Drawn as a rail rather than six separate bars because
 * the stages are sequential — the same person moves along it — and separate bars
 * read as unrelated categories.
 */
export function PipelineStrip({
  stages,
  deltas,
  compact = false,
}: {
  stages: Record<string, number>;
  /** Percent change per stage; null where there was no baseline to compare. */
  deltas?: Record<string, number | null>;
  /** Drop the labels and counts. Set when a chart above the rail already carries
   *  them — repeating the stage names and figures twice in one card just makes
   *  the card taller. */
  compact?: boolean;
}) {
  const last = PIPELINE_ORDER.length - 1;
  return (
    <ol className={compact ? "pipeline pipeline-compact" : "pipeline"}>
      {PIPELINE_ORDER.map((stage, index) => {
        const count = stages[stage] ?? 0;
        const delta = deltas?.[stage] ?? null;
        const classes = [
          "pipe-step",
          count > 0 ? "pipe-on" : "",
          index === last ? "pipe-end" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <li key={stage} className={classes}>
            {compact ? null : <span className="pipe-label">{humanise(stage)}</span>}
            <span className="pipe-track" aria-hidden="true">
              <span className="pipe-dot" />
            </span>
            {compact ? null : <span className="pipe-count">{count}</span>}
            {delta === null ? (
              // No prior activity to compare against. Left blank rather than
              // rendered as 0%, which would claim a measurement never taken.
              <span className="pipe-delta pipe-delta-none">&nbsp;</span>
            ) : (
              <span
                className={`pipe-delta ${delta > 0 ? "pipe-up" : delta < 0 ? "pipe-down" : ""}`}
              >
                {delta > 0 ? "+" : ""}
                {Math.round(delta)}%
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}



