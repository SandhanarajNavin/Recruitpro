/** Composite score as a donut. Colour tracks the same thresholds as the recommendation. */
export function ScoreRing({ score, size = 62 }: { score: number; size?: number }) {
  const stroke = 5;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const filled = (Math.max(0, Math.min(100, score)) / 100) * circumference;
  const colour = score >= 82 ? "var(--good)" : score >= 68 ? "var(--accent)" : score >= 52 ? "var(--warn)" : "var(--bad)";

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`Composite score ${score} out of 100`}>
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="var(--panel-border-strong)"
        strokeWidth={stroke}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={colour}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={`${filled} ${circumference - filled}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text
        x="50%"
        y="50%"
        textAnchor="middle"
        dominantBaseline="central"
        fill="var(--text)"
        fontSize={size * 0.29}
        fontFamily="var(--mono)"
        fontWeight={600}
      >
        {score.toFixed(0)}
      </text>
    </svg>
  );
}
