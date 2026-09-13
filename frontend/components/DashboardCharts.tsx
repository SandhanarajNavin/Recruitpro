"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PIPELINE_ORDER, humanise } from "@/components/ui";
import type { DayActivity } from "@/lib/types";

const AXIS = {
  stroke: "var(--text-faint)",
  fontSize: 11,
  tickLine: false,
  axisLine: false,
} as const;

/** Recharts renders its own tooltip chrome; this matches the app's cards. */
function Card({
  title,
  rows,
}: {
  title: string;
  rows: Array<{ label: string; value: number; colour?: string }>;
}) {
  return (
    <div className="chart-tip">
      <strong>{title}</strong>
      {rows.map((row) => (
        <span key={row.label}>
          {row.colour ? (
            <i className="chart-tip-dot" style={{ background: row.colour }} />
          ) : null}
          {row.label}: <b>{row.value}</b>
        </span>
      ))}
    </div>
  );
}

/**
 * Candidates currently standing at each pipeline stage.
 *
 * A bar chart, not a line: the stages are categories a person occupies, and a line
 * would imply the same population flowing continuously between them.
 */
export function PipelineBars({ stages }: { stages: Record<string, number> }) {
  const data = PIPELINE_ORDER.map((stage) => ({
    stage: humanise(stage),
    candidates: stages[stage] ?? 0,
  }));
  const peak = Math.max(...data.map((row) => row.candidates));

  return (
    <div className="chart-frame">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
          <CartesianGrid vertical={false} stroke="var(--panel-border)" />
          <XAxis dataKey="stage" {...AXIS} interval={0} />
          {/* An all-zero pipeline still needs a sane axis, or Recharts renders
              0–0 and the gridlines collapse onto one line. */}
          <YAxis allowDecimals={false} domain={[0, peak > 0 ? "auto" : 4]} {...AXIS} />
          <Tooltip
            cursor={{ fill: "var(--accent-soft)" }}
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <Card
                  title={String(label)}
                  rows={[{ label: "candidates", value: Number(payload[0].value ?? 0) }]}
                />
              ) : null
            }
          />
          <Bar
            dataKey="candidates"
            fill="var(--accent-strong)"
            radius={[4, 4, 0, 0]}
            maxBarSize={44}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

const WEEKDAY = new Intl.DateTimeFormat(undefined, { weekday: "short" });

/** Applications opened against hires made, over the last seven days. */
export function WeeklyActivity({ points }: { points: DayActivity[] }) {
  const data = points.map((point) => ({
    // Parsed as a local date; the label is a weekday, so an off-by-one timezone
    // shift would name the wrong day.
    day: WEEKDAY.format(new Date(`${point.day}T00:00:00`)),
    applied: point.applied,
    hired: point.hired,
  }));

  return (
    <div className="chart-frame">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
          <defs>
            <linearGradient id="fill-applied" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--accent-strong)" stopOpacity={0.22} />
              <stop offset="100%" stopColor="var(--accent-strong)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke="var(--panel-border)" />
          <XAxis dataKey="day" {...AXIS} interval={0} />
          <YAxis allowDecimals={false} domain={[0, "auto"]} {...AXIS} />
          <Tooltip
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <Card
                  title={String(label)}
                  rows={payload.map((entry) => ({
                    label: String(entry.name),
                    value: Number(entry.value ?? 0),
                    colour: String(entry.color),
                  }))}
                />
              ) : null
            }
          />
          <Area
            type="monotone"
            dataKey="applied"
            name="Applied"
            stroke="var(--accent-strong)"
            fill="url(#fill-applied)"
            strokeWidth={2}
          />
          <Area
            type="monotone"
            dataKey="hired"
            name="Hired"
            stroke="var(--good)"
            fill="transparent"
            strokeWidth={2}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
