"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

// ─── Types ────────────────────────────────────────────────────────────────────

type Confidence = "HIGH" | "MEDIUM" | "LOW";
type Direction = "positive" | "negative";

interface HeadlineMetric {
  id: string;
  label: string;
  value: string;
  range: { low: string; central: string; high: string } | null;
  direction: Direction;
  confidence: Confidence;
  context: string;
}

interface WaterfallStep {
  label: string;
  value: number;
  cumulative: number;
  type: "inflow" | "outflow" | "neutral" | "net";
  note: string | null;
}

interface WaterfallData {
  title: string;
  subtitle: string;
  household_profile: string;
  steps: WaterfallStep[];
  net_monthly: number;
  net_annual: number;
  pct_of_income: number;
}

interface CategoryItem {
  name: string;
  dollar_impact_monthly: { low: number; central: number; high: number };
  pct_change: { low: number; central: number; high: number };
  confidence: Confidence;
  explanation: string;
  note?: string;
}

interface TimelinePhase {
  label: string;
  cumulative_net_monthly: { low: number; central: number; high: number };
  what_happens: string[];
  mood: string;
  dominant_driver: string;
}

interface WinnerLoserProfile {
  profile: string;
  net_monthly_range: string;
  pct_of_income_range: string;
  why: string;
  confidence: Confidence;
  impact_quality?: string;
  caveat?: string;
  depends_on?: string;
}

interface GeographicRegion {
  id: string;
  name: string;
  examples: string;
  color: string;
  rent_impact_severity: "HIGH" | "MEDIUM" | "LOW";
  price_impact_severity: "HIGH" | "MEDIUM" | "LOW";
  net_monthly_range_median_hh: string;
  explanation: string;
  key_factor: string;
}

interface FullReport {
  meta: {
    pipeline_duration_seconds: number;
    total_tool_calls: number;
    agents_completed: string[];
    model_used: string;
  };
  policy: {
    title: string;
    one_liner: string;
    geography: string;
    estimated_annual_cost: string;
  };
  headline: {
    verdict: string;
    bottom_line: string;
    confidence: Confidence;
  };
  headline_metrics: HeadlineMetric[];
  waterfall: WaterfallData;
  category_breakdown: { categories: CategoryItem[] };
  timeline: { phases: TimelinePhase[]; household_profile: string };
  winners_losers: {
    winners: WinnerLoserProfile[];
    losers: WinnerLoserProfile[];
    mixed: WinnerLoserProfile[];
    distributional_verdict: { progressive_or_regressive: string; explanation: string };
  };
  geographic_impact: { regions: GeographicRegion[] };
  confidence_assessment: {
    weakest_link: string;
    what_would_change_conclusion: string[];
    by_component: { component: string; confidence: Confidence; reasoning: string }[];
  };
  narrative: {
    for_low_income: string;
    for_middle_income: string;
    for_upper_income: string;
    for_small_business: string;
    biggest_uncertainty: string;
  };
}

interface ResultsPanelProps {
  report: FullReport;
}

// ─── Color system ─────────────────────────────────────────────────────────────
// Red = only for genuinely negative economic outcomes
// Amber/orange = warnings, uncertainty, medium severity, trade-offs
// Slate/blue = neutral info, low confidence (not alarming)
// Emerald = gains, positive outcomes, high confidence

const CONF_STYLES: Record<Confidence, { pill: string; dot: string; label: string }> = {
  HIGH:   { pill: "border-emerald-500/30 bg-emerald-950/40 text-emerald-400", dot: "bg-emerald-400", label: "High confidence" },
  MEDIUM: { pill: "border-amber-500/25 bg-amber-950/30 text-amber-400",      dot: "bg-amber-400",   label: "Medium confidence" },
  LOW:    { pill: "border-slate-500/30 bg-slate-800/40 text-slate-400",       dot: "bg-slate-400",   label: "Low confidence" },
};

// Safe lookup — normalises any casing from the API, falls back to MEDIUM
function confStyle(raw: string | undefined | null) {
  if (!raw) return CONF_STYLES.MEDIUM;
  const key = raw.toUpperCase() as Confidence;
  return CONF_STYLES[key] ?? CONF_STYLES.MEDIUM;
}

// Severity: used for cost/risk indicators — not outcome badness
const SEVERITY_STYLES: Record<"HIGH" | "MEDIUM" | "LOW", { bar: string; text: string; label: string }> = {
  HIGH:   { bar: "bg-orange-400/70",  text: "text-orange-400",  label: "High" },
  MEDIUM: { bar: "bg-amber-400/60",   text: "text-amber-400",   label: "Medium" },
  LOW:    { bar: "bg-teal-400/60",    text: "text-teal-400",    label: "Low" },
};

// ─── Shared atoms ─────────────────────────────────────────────────────────────

function ConfBadge({ c }: { c: Confidence }) {
  return (
    <span className={cn("inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium", CONF_STYLES[c].pill)}>
      <span className={cn("mr-1.5 h-1.5 w-1.5 rounded-full", CONF_STYLES[c].dot)} />
      {CONF_STYLES[c].label}
    </span>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 12 12" fill="none"
      className={cn("shrink-0 text-white/30 transition-transform duration-200", open && "rotate-180")}>
      <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-2xl border border-white/10 bg-[var(--bg-surface)] p-5", className)}>
      {children}
    </div>
  );
}

// Section headings — readable, not decorative
function SectionHeading({ label, sub }: { label: string; sub?: string }) {
  return (
    <div className="mb-5">
      <h3 className="text-base font-semibold text-white/85">{label}</h3>
      {sub && <p className="mt-0.5 text-sm text-white/45">{sub}</p>}
    </div>
  );
}

// Step indicator for the page narrative flow
function StepBadge({ n, label }: { n: number; label: string }) {
  return (
    <div className="mb-4 flex items-center gap-3">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-white/15 bg-white/8 text-xs font-semibold text-white/60">
        {n}
      </div>
      <span className="text-xs font-medium uppercase tracking-widest text-white/40">{label}</span>
      <div className="h-px flex-1 bg-white/8" />
    </div>
  );
}

// ─── 1: Hero header ───────────────────────────────────────────────────────────

function PolicyHeader({ report }: { report: FullReport }) {
  const pos = report.headline_metrics.filter((m) => m.direction === "positive").length;
  const neg = report.headline_metrics.filter((m) => m.direction === "negative").length;
  const total = pos + neg || 1;
  const posW = Math.round((pos / total) * 100);

  const confStyle = CONF_STYLES[report.headline.confidence];

  return (
    <Card className="p-6">
      {/* Tags row */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="rounded-full border border-white/12 bg-white/6 px-3 py-1 text-xs text-white/55">
          {report.policy.geography}
        </span>
        <span className="rounded-full border border-white/12 bg-white/6 px-3 py-1 text-xs text-white/55">
          {report.meta.agents_completed.length} agents · {report.meta.total_tool_calls} data calls
        </span>
        {report.policy.estimated_annual_cost && (
          <span className="rounded-full border border-orange-500/25 bg-orange-950/25 px-3 py-1 text-xs text-orange-300/80">
            Federal cost: {report.policy.estimated_annual_cost}/yr
          </span>
        )}
        <span className={cn("ml-auto rounded-full border px-3 py-1 text-xs font-medium", confStyle.pill)}>
          {confStyle.label}
        </span>
      </div>

      {/* Title */}
      <h1 className="mb-1 text-2xl font-semibold leading-snug tracking-tight text-white">
        {report.policy.title}
      </h1>
      <p className="mb-5 text-base text-white/50">{report.policy.one_liner}</p>

      {/* Verdict callout */}
      <div className="mb-5 rounded-xl border-l-4 border-emerald-500/60 bg-emerald-950/20 px-4 py-3">
        <div className="mb-0.5 text-xs font-semibold uppercase tracking-widest text-emerald-400/70">
          Overall verdict
        </div>
        <p className="text-[15px] font-medium leading-snug text-white/90">{report.headline.verdict}</p>
      </div>

      {/* Signal bar */}
      <div className="space-y-2.5">
        <div className="flex items-center justify-between text-sm text-white/50">
          <span>{pos} positive outcomes</span>
          <span>{neg} costs / risks</span>
        </div>
        <div className="flex h-3 overflow-hidden rounded-full bg-white/8">
          <div className="bg-emerald-500/65 transition-all duration-700" style={{ width: `${posW}%` }} />
          <div className="flex-1 bg-red-500/45" />
        </div>
        <div className="flex justify-between text-xs text-white/35">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-emerald-500/65" />Gains
          </span>
          <span className="flex items-center gap-1.5">
            Costs &amp; risks
            <span className="h-2 w-2 rounded-full bg-red-500/45" />
          </span>
        </div>
      </div>
    </Card>
  );
}

// ─── 2: KPI strip ─────────────────────────────────────────────────────────────

function KpiStrip({ metrics }: { metrics: HeadlineMetric[] }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {metrics.slice(0, 5).map((m) => (
        <div
          key={m.id}
          className={cn(
            "rounded-xl border p-4",
            m.direction === "positive"
              ? "border-emerald-500/20 bg-emerald-950/15"
              : "border-red-500/20 bg-red-950/15",
          )}
        >
          <div className="mb-2 text-xs font-medium text-white/50 leading-tight">{m.label}</div>
          <div className={cn("text-[22px] font-semibold leading-none tracking-tight",
            m.direction === "positive" ? "text-emerald-300" : "text-red-300")}>
            {m.value}
          </div>
          {m.range && (
            <div className="mt-1 text-[11px] text-white/28 tabular-nums">
              {m.range.low} – {m.range.high}
            </div>
          )}
          <div className="mt-2.5">
            <ConfBadge c={m.confidence} />
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── 3: Waterfall chart — SVG ─────────────────────────────────────────────────

function WaterfallChart({ data }: { data: WaterfallData }) {
  const [hoveredStep, setHoveredStep] = useState<string | null>(null);
  const steps = data.steps.filter((s) => s.value !== 0 && s.type !== "neutral");
  const maxVal = Math.max(...steps.map((s) => Math.abs(s.value)));

  const BAR_H = 34;
  const BAR_GAP = 5;
  const LABEL_W = 156;
  const TRACK_W = 280;
  const CUM_W = 64;
  const PAD = 10;
  const svgH = steps.length * (BAR_H + BAR_GAP) + PAD * 2;
  const SVG_W = LABEL_W + TRACK_W + CUM_W + PAD * 3;

  return (
    <div>
      <div className="mb-1 text-sm text-white/50">{data.subtitle}</div>
      <div className="mb-5 flex items-center gap-4 text-xs text-white/35">
        <span>{data.household_profile}</span>
        <span className="ml-auto flex items-center gap-3">
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded bg-emerald-500/50" />Gain</span>
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded bg-red-500/40" />Cost</span>
          <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded bg-emerald-500/70" />Net</span>
          <span className="text-white/20">Cumulative →</span>
        </span>
      </div>

      <div className="overflow-x-auto">
        <svg width="100%" viewBox={`0 0 ${SVG_W} ${svgH}`} style={{ minWidth: 460 }}>
          {steps.map((step, i) => {
            const isNet = step.type === "net";
            const isInflow = step.type === "inflow";
            const pct = Math.round((Math.abs(step.value) / maxVal) * 100);
            const barW = Math.max(4, Math.round((pct / 100) * TRACK_W));
            const y = PAD + i * (BAR_H + BAR_GAP);
            const isHovered = hoveredStep === step.label;

            const barFill = isNet
              ? "rgba(52,211,153,0.65)"
              : isInflow
                ? "rgba(52,211,153,0.38)"
                : "rgba(239,68,68,0.35)";
            const textFill = isNet ? "#6ee7b7" : isInflow ? "#a7f3d0" : "#fca5a5";
            const labelFill = isNet
              ? "rgba(255,255,255,0.90)"
              : isHovered
                ? "rgba(255,255,255,0.75)"
                : "rgba(255,255,255,0.55)";

            return (
              <g key={`${step.label}-${i}`}
                className="cursor-default"
                onMouseEnter={() => setHoveredStep(step.label)}
                onMouseLeave={() => setHoveredStep(null)}>
                {/* Label */}
                <text x={LABEL_W - 10} y={y + BAR_H / 2}
                  textAnchor="end" dominantBaseline="central"
                  fill={labelFill}
                  fontSize={isNet ? "13" : "12"}
                  fontWeight={isNet ? "600" : "400"}>
                  {step.label}
                </text>

                {/* Track bg */}
                <rect x={LABEL_W + PAD} y={y} width={TRACK_W} height={BAR_H} rx="6"
                  fill={isHovered ? "rgba(255,255,255,0.06)" : "rgba(255,255,255,0.03)"} />

                {/* Bar */}
                <rect x={LABEL_W + PAD} y={y} width={barW} height={BAR_H} rx="6" fill={barFill} />

                {/* Value label in bar */}
                {barW > 54 && (
                  <text x={LABEL_W + PAD + barW - 10} y={y + BAR_H / 2}
                    textAnchor="end" dominantBaseline="central"
                    fill={textFill} fontSize="12" fontWeight="500">
                    {isInflow ? "+" : ""}${Math.abs(step.value)}
                  </text>
                )}
                {barW <= 54 && (
                  <text x={LABEL_W + PAD + barW + 6} y={y + BAR_H / 2}
                    dominantBaseline="central"
                    fill={textFill} fontSize="11">
                    {isInflow ? "+" : "–"}${Math.abs(step.value)}
                  </text>
                )}

                {/* Cumulative */}
                <text x={LABEL_W + PAD + TRACK_W + 10} y={y + BAR_H / 2}
                  dominantBaseline="central"
                  fill="rgba(255,255,255,0.28)" fontSize="11" fontFamily="monospace">
                  ${step.cumulative}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Net summary */}
      <div className="mt-5 grid grid-cols-3 gap-3">
        {[
          { label: "Net monthly gain", value: `+$${data.net_monthly}`, big: true },
          { label: "Over a year", value: `+$${data.net_annual.toLocaleString()}` },
          { label: "% of income", value: `+${data.pct_of_income}%` },
        ].map((s) => (
          <div key={s.label}
            className="rounded-xl border border-emerald-500/20 bg-emerald-950/15 p-3 text-center">
            <div className="text-xs text-white/40">{s.label}</div>
            <div className={cn("mt-1 font-semibold text-emerald-300", s.big ? "text-2xl" : "text-lg")}>
              {s.value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── 4: Category chart — horizontal SVG bars ─────────────────────────────────

function CategoryChart({ categories }: { categories: CategoryItem[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const sorted = [...categories].sort(
    (a, b) => Math.abs(b.dollar_impact_monthly.central) - Math.abs(a.dollar_impact_monthly.central),
  );
  const maxAbs = Math.max(...sorted.map((c) => Math.abs(c.dollar_impact_monthly.central)));

  const BAR_H = 30;
  const BAR_GAP = 7;
  const LABEL_W = 148;
  const TRACK_W = 240;
  const VAL_W = 80;
  const PAD = 8;
  const svgH = sorted.length * (BAR_H + BAR_GAP) + PAD * 2;
  const SVG_W = LABEL_W + TRACK_W + VAL_W + PAD * 2;

  return (
    <div>
      <div className="overflow-x-auto">
        <svg width="100%" viewBox={`0 0 ${SVG_W} ${svgH}`} style={{ minWidth: 420 }}>
          {sorted.map((cat, i) => {
            const isNeg = cat.dollar_impact_monthly.central < 0;
            const pct = Math.round((Math.abs(cat.dollar_impact_monthly.central) / maxAbs) * 100);
            const barW = Math.max(4, Math.round((pct / 100) * TRACK_W));
            const y = PAD + i * (BAR_H + BAR_GAP);
            // Costs use orange, not pure red — it's a cost, not a catastrophe
            const barFill = isNeg ? "rgba(249,115,22,0.38)" : "rgba(52,211,153,0.38)";
            const valFill = isNeg ? "#fdba74" : "#6ee7b7";
            const isActive = expanded === cat.name;

            return (
              <g key={cat.name} className="cursor-pointer"
                onClick={() => setExpanded(isActive ? null : cat.name)}>
                <text x={LABEL_W - 10} y={y + BAR_H / 2}
                  textAnchor="end" dominantBaseline="central"
                  fill={isActive ? "rgba(255,255,255,0.85)" : "rgba(255,255,255,0.60)"}
                  fontSize="12">
                  {cat.name}
                </text>
                <rect x={LABEL_W + PAD} y={y} width={TRACK_W} height={BAR_H} rx="5"
                  fill={isActive ? "rgba(255,255,255,0.06)" : "rgba(255,255,255,0.04)"} />
                <rect x={LABEL_W + PAD} y={y} width={barW} height={BAR_H} rx="5" fill={barFill} />
                {barW > 36 && (
                  <text x={LABEL_W + PAD + 8} y={y + BAR_H / 2}
                    dominantBaseline="central" fill={valFill} fontSize="11" fontWeight="500">
                    {isNeg ? "" : "+"}{cat.pct_change.central}%
                  </text>
                )}
                <text x={LABEL_W + PAD + TRACK_W + 10} y={y + BAR_H / 2}
                  dominantBaseline="central"
                  fill={valFill} fontSize="12" fontWeight="500" fontFamily="monospace">
                  {isNeg ? "–" : "+"}${Math.abs(cat.dollar_impact_monthly.central)}/mo
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Expanded detail — outside SVG */}
      {expanded && (() => {
        const cat = sorted.find((c) => c.name === expanded);
        if (!cat) return null;
        return (
          <div className="mt-3 rounded-xl border border-white/10 bg-white/[0.03] p-4">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-white/85">{cat.name}</span>
              <ConfBadge c={cat.confidence} />
            </div>
            <p className="mb-2 text-sm text-white/55">{cat.explanation}</p>
            {cat.note && <p className="mb-2 text-xs text-amber-300/70">{cat.note}</p>}
            <div className="text-xs text-white/30 tabular-nums">
              Range: ${cat.dollar_impact_monthly.low} – ${cat.dollar_impact_monthly.high}/mo
            </div>
          </div>
        );
      })()}
    </div>
  );
}

// ─── 5: Timeline chart ────────────────────────────────────────────────────────

function TimelineChart({ phases, household_profile }: { phases: TimelinePhase[]; household_profile: string }) {
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const maxVal = Math.max(...phases.map((p) => p.cumulative_net_monthly.high));
  const W = 540;
  const H = 150;
  const PL = 48; const PR = 20; const PT = 14; const PB = 34;
  const iW = W - PL - PR; const iH = H - PT - PB;
  const xOf = (i: number) => PL + (i / (phases.length - 1)) * iW;
  const yOf = (v: number) => PT + iH - (v / maxVal) * iH;

  const highPts = phases.map((p, i) => `${xOf(i)},${yOf(p.cumulative_net_monthly.high)}`).join(" ");
  const lowPtsRev = [...phases].reverse().map((p, i) =>
    `${xOf(phases.length - 1 - i)},${yOf(p.cumulative_net_monthly.low)}`
  ).join(" ");
  const centralPts = phases.map((p, i) => `${xOf(i)},${yOf(p.cumulative_net_monthly.central)}`).join(" ");

  // Mood → line color (no red — all phases net positive in this schema)
  const moodColor: Record<string, string> = {
    optimistic: "#34d399", stable: "#60a5fa", settling: "#f59e0b",
    new_normal: "#c4b5fd", uncertain: "#94a3b8",
  };

  return (
    <div>
      <div className="mb-4 text-xs text-white/35">{household_profile}</div>
      <div className="overflow-x-auto">
        <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ minWidth: 380 }}>
          {/* Y gridlines */}
          {[0, Math.round(maxVal * 0.5), maxVal].map((t) => (
            <g key={t}>
              <line x1={PL} y1={yOf(t)} x2={W - PR} y2={yOf(t)}
                stroke="rgba(255,255,255,0.06)" strokeWidth="1" />
              <text x={PL - 6} y={yOf(t)} textAnchor="end" dominantBaseline="central"
                fill="rgba(255,255,255,0.28)" fontSize="10">
                ${t}
              </text>
            </g>
          ))}

          {/* Confidence band */}
          <polygon points={`${highPts} ${lowPtsRev}`} fill="rgba(52,211,153,0.08)" />

          {/* Central line */}
          <polyline points={centralPts} fill="none"
            stroke="#34d399" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />

          {/* Points + interaction */}
          {phases.map((p, i) => {
            const x = xOf(i); const y = yOf(p.cumulative_net_monthly.central);
            const active = activeIdx === i;
            const color = moodColor[p.mood] ?? "#94a3b8";
            return (
              <g key={`pt-${i}`}
                onMouseEnter={() => setActiveIdx(i)}
                onMouseLeave={() => setActiveIdx(null)}>
                {active && (
                  <line x1={x} y1={PT} x2={x} y2={H - PB}
                    stroke="rgba(255,255,255,0.12)" strokeWidth="1" strokeDasharray="3 3" />
                )}
                <circle cx={x} cy={y} r={active ? 5 : 3.5} fill={color}
                  stroke="#111318" strokeWidth="2" className="cursor-pointer" />
                {/* Tooltip */}
                {active && (
                  <g>
                    <rect x={Math.min(x - 65, W - 145)} y={y - 50}
                      width={140} height={42} rx="6"
                      fill="#181C23" stroke="rgba(255,255,255,0.12)" strokeWidth="0.5" />
                    <text x={Math.min(x - 65, W - 145) + 10} y={y - 33}
                      fill="rgba(255,255,255,0.88)" fontSize="13" fontWeight="600">
                      +${p.cumulative_net_monthly.central}/mo
                    </text>
                    <text x={Math.min(x - 65, W - 145) + 10} y={y - 18}
                      fill="rgba(255,255,255,0.38)" fontSize="10">
                      range ${p.cumulative_net_monthly.low}–${p.cumulative_net_monthly.high}
                    </text>
                  </g>
                )}
                {/* X label */}
                <text x={x} y={H - PB + 14} textAnchor="middle"
                  fill="rgba(255,255,255,0.35)" fontSize="10">
                  {p.label}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Phase rows */}
      <div className="mt-3 space-y-1.5">
        {phases.map((p, i) => {
          const color = moodColor[p.mood] ?? "#94a3b8";
          const isActive = activeIdx === i;
          return (
            <div key={`row-${i}`}
              className={cn(
                "flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors",
                isActive ? "border-white/14 bg-white/5" : "border-white/6 hover:border-white/10",
              )}
              onMouseEnter={() => setActiveIdx(i)}
              onMouseLeave={() => setActiveIdx(null)}>
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: color }} />
              <span className="w-20 shrink-0 text-sm font-medium text-white/65">{p.label}</span>
              <span className="flex-1 truncate text-sm text-white/38">{p.dominant_driver}</span>
              <span className="shrink-0 tabular-nums text-sm font-semibold text-emerald-300">
                +${p.cumulative_net_monthly.central}/mo
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── 6: Winners & Losers ──────────────────────────────────────────────────────

// ProfileCard as standalone component — NO useState in map()
function ProfileCard({
  profile,
  cardType,
}: {
  profile: WinnerLoserProfile;
  cardType: "winner" | "loser" | "mixed";
}) {
  const [open, setOpen] = useState(false);

  const styles = {
    winner: {
      border: "border-emerald-500/20",
      bg: "bg-emerald-950/12",
      dot: "bg-emerald-400",
      num: "text-emerald-300",
    },
    loser: {
      // Losers: use orange/coral — it's a bad outcome but not alarming red
      border: "border-orange-500/20",
      bg: "bg-orange-950/10",
      dot: "bg-orange-400",
      num: "text-orange-300",
    },
    mixed: {
      border: "border-amber-500/18",
      bg: "bg-amber-950/10",
      dot: "bg-amber-400",
      num: "text-amber-300",
    },
  }[cardType];

  return (
    <div className={cn("rounded-xl border p-4", styles.border, styles.bg)}>
      <div className="mb-2 flex items-start gap-2">
        <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", styles.dot)} />
        <span className="text-sm font-semibold leading-snug text-white/85">{profile.profile}</span>
      </div>
      <div className={cn("mb-1 text-xl font-semibold tabular-nums leading-none", styles.num)}>
        {profile.net_monthly_range}
        <span className="ml-1 text-sm font-normal text-white/35">/mo</span>
      </div>
      {profile.pct_of_income_range !== "N/A" && (
        <div className="mb-3 text-xs text-white/35">{profile.pct_of_income_range} of income</div>
      )}
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-xs text-white/35 hover:text-white/60 transition-colors"
      >
        Why? <Chevron open={open} />
      </button>
      {open && (
        <div className="mt-3 space-y-2 border-t border-white/8 pt-3">
          <p className="text-sm leading-6 text-white/55">{profile.why}</p>
          {profile.caveat && (
            <p className="text-xs text-amber-300/65">{profile.caveat}</p>
          )}
          {profile.depends_on && (
            <p className="text-xs text-white/30">Depends on: {profile.depends_on}</p>
          )}
          <ConfBadge c={profile.confidence} />
        </div>
      )}
    </div>
  );
}

function WinnersLosers({ data }: { data: FullReport["winners_losers"] }) {
  type WLTab = "winners" | "losers" | "mixed";
  const [tab, setTab] = useState<WLTab>("winners");

  const tabs: { key: WLTab; label: string; activeClass: string; count: number }[] = [
    {
      key: "winners", label: "Winners", count: data.winners.length,
      activeClass: "border-emerald-500/35 bg-emerald-950/30 text-emerald-300",
    },
    {
      key: "losers", label: "Losers / risks", count: data.losers.length,
      activeClass: "border-orange-500/35 bg-orange-950/25 text-orange-300",
    },
    {
      key: "mixed", label: "Mixed", count: data.mixed.length,
      activeClass: "border-amber-500/30 bg-amber-950/25 text-amber-300",
    },
  ];

  const profiles: Record<WLTab, WinnerLoserProfile[]> = {
    winners: data.winners,
    losers: data.losers,
    mixed: data.mixed,
  };

  return (
    <div className="space-y-4">
      {/* Distributional verdict */}
      <div className="rounded-xl border border-emerald-500/20 bg-emerald-950/15 px-5 py-4">
        <div className="mb-1 text-xs font-semibold uppercase tracking-widest text-emerald-400/70">
          {data.distributional_verdict.progressive_or_regressive}
        </div>
        <p className="text-sm leading-6 text-white/65">{data.distributional_verdict.explanation}</p>
      </div>

      {/* Tab row */}
      <div className="flex gap-2">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              "rounded-full border px-4 py-1.5 text-sm font-medium transition-all duration-150",
              tab === t.key ? t.activeClass : "border-white/10 text-white/40 hover:text-white/65",
            )}
          >
            {t.label}
            <span className="ml-1.5 text-xs opacity-60">({t.count})</span>
          </button>
        ))}
      </div>

      {/* Cards */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {profiles[tab].map((p) => (
          <ProfileCard key={p.profile} profile={p} cardType={tab} />
        ))}
      </div>
    </div>
  );
}

// ─── 7: Geographic impact ─────────────────────────────────────────────────────

function GeographicImpact({ regions }: { regions: GeographicRegion[] }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      {regions.map((r) => (
        <div key={r.id} className="rounded-xl border border-white/10 bg-[var(--bg-surface)] p-5">
          <div className="mb-1 text-base font-semibold text-white/88">{r.name}</div>
          <div className="mb-4 text-xs text-white/35">{r.examples}</div>

          {/* Big net number */}
          <div className="mb-4 text-2xl font-semibold" style={{ color: r.color }}>
            {r.net_monthly_range_median_hh}
            <span className="ml-1 text-sm font-normal text-white/30">/mo</span>
          </div>

          {/* Cost severity meters — orange for high, amber for medium */}
          <div className="mb-4 space-y-2.5">
            {[
              { label: "Rent pressure", severity: r.rent_impact_severity },
              { label: "Price pressure", severity: r.price_impact_severity },
            ].map((s) => (
              <div key={s.label}>
                <div className="mb-1.5 flex justify-between text-xs text-white/40">
                  <span>{s.label}</span>
                  <span className={SEVERITY_STYLES[s.severity].text}>
                    {SEVERITY_STYLES[s.severity].label}
                  </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-white/8">
                  <div
                    className={cn("h-full rounded-full transition-all", SEVERITY_STYLES[s.severity].bar)}
                    style={{ width: s.severity === "HIGH" ? "75%" : s.severity === "MEDIUM" ? "48%" : "22%" }}
                  />
                </div>
              </div>
            ))}
          </div>

          <p className="text-xs leading-4 text-white/40">{r.key_factor}</p>
        </div>
      ))}
    </div>
  );
}

// ─── 8: Confidence radar — SVG pentagon ──────────────────────────────────────

function ConfidenceRadar({ data }: { data: FullReport["confidence_assessment"] }) {
  const [expandedScenario, setExpandedScenario] = useState(false);
  const components = data.by_component.slice(0, 5);
  const scores: Record<Confidence, number> = { HIGH: 1, MEDIUM: 0.55, LOW: 0.2 };

  const SIZE = 180;
  const cx = SIZE / 2; const cy = SIZE / 2;
  const R = 68; const n = components.length;

  function polarPt(i: number, r: number) {
    const angle = (i * 2 * Math.PI) / n - Math.PI / 2;
    return { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) };
  }

  const gridLevels = [0.33, 0.67, 1];
  const dataPoints = components.map((c, i) => polarPt(i, R * scores[c.confidence]));
  const dataPath = dataPoints.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ") + "Z";

  return (
    <div className="flex flex-col gap-5 sm:flex-row sm:items-start">
      {/* Pentagon chart */}
      <div className="shrink-0 self-center sm:self-start">
        <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}>
          {gridLevels.map((lvl) => {
            const pts = Array.from({ length: n }, (_, i) => polarPt(i, R * lvl));
            const d = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ") + "Z";
            return <path key={lvl} d={d} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="1" />;
          })}
          {components.map((_, i) => {
            const outer = polarPt(i, R);
            return <line key={i} x1={cx} y1={cy} x2={outer.x} y2={outer.y}
              stroke="rgba(255,255,255,0.06)" strokeWidth="1" />;
          })}
          <path d={dataPath} fill="rgba(96,165,250,0.18)" stroke="#60a5fa" strokeWidth="1.5" />
          {dataPoints.map((p, i) => (
            <circle key={i} cx={p.x} cy={p.y} r="3.5" fill="#60a5fa" stroke="#111318" strokeWidth="2" />
          ))}
          {components.map((c, i) => {
            const pt = polarPt(i, R + 20);
            return (
              <text key={i} x={pt.x} y={pt.y} textAnchor="middle" dominantBaseline="central"
                fill="rgba(255,255,255,0.45)" fontSize="10">
                {c.component.split(" ").slice(0, 2).join(" ")}
              </text>
            );
          })}
        </svg>
      </div>

      <div className="flex-1 space-y-2.5">
        {/* Weakest link — amber, not red */}
        <div className="rounded-xl border border-amber-500/20 bg-amber-950/15 px-4 py-3">
          <div className="mb-1 text-xs font-semibold uppercase tracking-widest text-amber-400/70">
            Biggest uncertainty
          </div>
          <p className="text-sm font-medium text-white/80">{data.weakest_link}</p>
        </div>

        {/* Component confidence rows */}
        {components.map((c) => (
          <div key={c.component} className="flex items-center gap-3 rounded-lg border border-white/7 px-3 py-2.5">
            <ConfBadge c={c.confidence} />
            <span className="flex-1 text-sm text-white/65">{c.component}</span>
          </div>
        ))}

        {/* What would change */}
        <button
          onClick={() => setExpandedScenario(!expandedScenario)}
          className="flex w-full items-center gap-2 rounded-lg border border-white/7 px-3 py-2.5 text-sm text-white/45 hover:text-white/65 transition-colors"
        >
          <span className="flex-1 text-left">
            {data.what_would_change_conclusion.length} scenarios that change this conclusion
          </span>
          <Chevron open={expandedScenario} />
        </button>
        {expandedScenario && (
          <div className="space-y-2 rounded-xl border border-white/8 bg-white/[0.02] p-3">
            {data.what_would_change_conclusion.map((item, i) => (
              <div key={i} className="flex gap-2.5 text-sm text-white/50">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400/50" />
                {item}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── 9: Narrative — audience tabs (FIXED: text keyed correctly) ───────────────

type NarrativeTab = "low_income" | "middle_income" | "upper_income" | "small_business";

function NarrativePanel({ narrative }: { narrative: FullReport["narrative"] }) {
  const [activeTab, setActiveTab] = useState<NarrativeTab>("middle_income");

  const tabs: { key: NarrativeTab; label: string }[] = [
    { key: "low_income",    label: "Low income" },
    { key: "middle_income", label: "Middle income" },
    { key: "upper_income",  label: "Upper income" },
    { key: "small_business", label: "Small business" },
  ];

  // Explicit mapping — no computed key indexing, avoids silent mismatch bugs
  function getContent(tab: NarrativeTab): string {
    if (tab === "low_income")    return narrative.for_low_income;
    if (tab === "middle_income") return narrative.for_middle_income;
    if (tab === "upper_income")  return narrative.for_upper_income;
    return narrative.for_small_business;
  }

  return (
    <div>
      {/* Tab row */}
      <div className="mb-4 flex flex-wrap gap-2">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={cn(
              "rounded-full border px-4 py-1.5 text-sm font-medium transition-all duration-150",
              activeTab === t.key
                ? "border-white/22 bg-white/10 text-white/90"
                : "border-white/8 text-white/40 hover:text-white/65",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content — always rendered fresh from getContent() */}
      <p className="mb-5 text-sm leading-7 text-white/65">
        {getContent(activeTab)}
      </p>

      <div className="rounded-xl border border-amber-500/18 bg-amber-950/12 px-4 py-3">
        <div className="mb-1 text-xs font-semibold uppercase tracking-widest text-amber-400/65">
          Key uncertainty
        </div>
        <p className="text-sm text-white/55">{narrative.biggest_uncertainty}</p>
      </div>
    </div>
  );
}

// ─── Main — page flow ─────────────────────────────────────────────────────────

export default function ResultsPanel({ report }: ResultsPanelProps) {
  return (
    <section className="mx-auto w-full max-w-5xl space-y-6 px-4 py-6 sm:px-6">

      {/* ── Step 1: What is this policy? ── */}
      <StepBadge n={1} label="Policy overview" />
      <PolicyHeader report={report} />
      <KpiStrip metrics={report.headline_metrics} />

      {/* ── Step 2: Where does the money go? ── */}
      <StepBadge n={2} label="Where does the money go?" />
      <Card>
        <SectionHeading
          label={report.waterfall.title}
          sub="Every dollar of the transfer, traced through taxes, rent, and prices to your net monthly gain"
        />
        <WaterfallChart data={report.waterfall} />
      </Card>

      {/* ── Step 3: What gets more expensive? ── */}
      <StepBadge n={3} label="What gets more expensive?" />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <SectionHeading
            label="Spending category impacts"
            sub="How much each budget line rises — click any row for detail"
          />
          {report.category_breakdown?.categories && (
            <CategoryChart categories={report.category_breakdown.categories} />
          )}
        </Card>

        <Card>
          <SectionHeading
            label="When will you feel it?"
            sub="Net monthly benefit over time as prices adjust"
          />
          {report.timeline?.phases && (
            <TimelineChart
              phases={report.timeline.phases}
              household_profile={report.timeline.household_profile}
            />
          )}
        </Card>
      </div>

      {/* ── Step 4: Who wins and who loses? ── */}
      <StepBadge n={4} label="Who wins and who loses?" />
      <Card>
        <SectionHeading
          label="Who benefits — who doesn't"
          sub="Outcomes vary dramatically by income level, housing tenure, and geography"
        />
        {report.winners_losers && <WinnersLosers data={report.winners_losers} />}
      </Card>

      {/* ── Step 5: Does location matter? ── */}
      <StepBadge n={5} label="Does location matter?" />
      <Card>
        <SectionHeading
          label="Impact by region"
          sub="High-cost cities absorb more of the UBI through rent. Low-cost areas keep more."
        />
        {report.geographic_impact?.regions && (
          <GeographicImpact regions={report.geographic_impact.regions} />
        )}
      </Card>

      {/* ── Step 6: How certain is this? ── */}
      <StepBadge n={6} label="How certain is this?" />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <SectionHeading
            label="Confidence by component"
            sub="Closer to the edge = higher confidence. Inner = more uncertain."
          />
          {report.confidence_assessment && (
            <ConfidenceRadar data={report.confidence_assessment} />
          )}
        </Card>

        <Card>
          <SectionHeading
            label="What this means for you"
            sub="Select your situation to see a plain-language summary"
          />
          {report.narrative && <NarrativePanel narrative={report.narrative} />}
        </Card>
      </div>

      {/* Footer */}
      <footer className="pt-2 text-center text-xs text-white/25">
        {report.meta.total_tool_calls} tool calls ·{" "}
        {report.meta.agents_completed.length} agents ·{" "}
        {report.meta.pipeline_duration_seconds}s ·{" "}
        {report.meta.model_used}
      </footer>
    </section>
  );
}