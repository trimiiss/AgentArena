import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { Table, Td, Th } from "./ui";

/* Charts follow one spec: 2px lines, thin bars with a 4px rounded data-end, solid
   hairline grid, one y-axis, recessive axes, and text in text tokens. Hover is
   matched by keyboard focus, and a table view keeps every value reachable. */

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(element.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Rounded axis steps (1, 2, 2.5 or 5 times a power of ten). */
export function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0, 1];
  const rough = max / count;
  const power = 10 ** Math.floor(Math.log10(rough));
  const unit = [1, 2, 2.5, 5, 10].map((step) => step * power).find((step) => step >= rough) ?? rough;
  const ticks: number[] = [];
  for (let value = 0; value < max + unit * 0.999; value += unit) ticks.push(Number(value.toFixed(6)));
  return ticks;
}

export interface LineSeries {
  key: string;
  label: string;
  color: string;
  values: (number | null)[];
}

interface LineChartProps {
  title: string;
  labels: string[];
  series: LineSeries[];
  format: (value: number) => string;
  tickFormat?: (value: number) => string;
  domainMax?: number;
  height?: number;
}

const PLOT = { top: 12, right: 16, bottom: 26, left: 52 };

export function LineChart({ title, labels, series, format, tickFormat, domainMax, height = 220 }: LineChartProps) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);

  const peak = useMemo(() => Math.max(0, ...series.flatMap((item) => item.values.map((value) => value ?? 0))), [series]);
  const ticks = useMemo(() => (domainMax ? niceTicks(domainMax, 4) : niceTicks(peak * 1.05 || 1, 4)), [domainMax, peak]);
  const top = ticks[ticks.length - 1];

  const inner = Math.max(0, width - PLOT.left - PLOT.right);
  const plotHeight = height - PLOT.top - PLOT.bottom;
  const x = (index: number) => PLOT.left + (labels.length > 1 ? (index / (labels.length - 1)) * inner : inner / 2);
  const y = (value: number) => PLOT.top + plotHeight - (value / top) * plotHeight;

  function nearest(event: PointerEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = inner ? (event.clientX - box.left - PLOT.left) / inner : 0;
    setActive(Math.min(labels.length - 1, Math.max(0, Math.round(ratio * (labels.length - 1)))));
  }

  function onKey(event: KeyboardEvent<SVGSVGElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    setActive((current) => Math.min(labels.length - 1, Math.max(0, (current ?? labels.length - 1) + step)));
  }

  const path = (values: (number | null)[]) => {
    let command = "";
    let open = false;
    values.forEach((value, index) => {
      if (value === null) {
        open = false;
        return;
      }
      command += `${open ? "L" : "M"}${x(index).toFixed(1)} ${y(value).toFixed(1)}`;
      open = true;
    });
    return command;
  };

  const tooltipLeft = active === null ? 0 : Math.min(Math.max(x(active) + 10, 0), Math.max(0, width - 170));

  return (
    <div>
      {series.length > 1 && <Legend items={series.map((item) => ({ label: item.label, color: item.color, kind: "line" }))} />}
      <div ref={ref} className="relative" style={{ height }}>
        {width > 0 && (
          <svg
            width={width}
            height={height}
            role="img"
            tabIndex={0}
            aria-label={`${title}. Use the left and right arrow keys to read values.`}
            onPointerMove={nearest}
            onPointerLeave={() => setActive(null)}
            onFocus={() => setActive(labels.length - 1)}
            onBlur={() => setActive(null)}
            onKeyDown={onKey}
            className="block touch-pan-y"
          >
            {ticks.map((tick) => (
              <g key={tick}>
                <line x1={PLOT.left} x2={width - PLOT.right} y1={y(tick)} y2={y(tick)} stroke="var(--line)" strokeWidth={1} />
                <text x={PLOT.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--axis)" className="tabular">
                  {(tickFormat ?? format)(tick)}
                </text>
              </g>
            ))}
            {labels.map((label, index) => {
              const every = Math.max(1, Math.ceil(labels.length / Math.max(2, Math.floor(inner / 90))));
              const last = index === labels.length - 1;
              if (index % every !== 0 && !last) return null;
              if (!last && labels.length - 1 - index < every / 2) return null;
              return (
                <text key={label + index} x={x(index)} y={height - 8} textAnchor={last && labels.length > 1 ? "end" : index === 0 && labels.length > 1 ? "start" : "middle"} fontSize={11} fill="var(--axis)">
                  {label}
                </text>
              );
            })}
            {active !== null && <line x1={x(active)} x2={x(active)} y1={PLOT.top} y2={PLOT.top + plotHeight} stroke="var(--line-strong)" strokeWidth={1} />}
            {series.map((item) => (
              <g key={item.key}>
                <path d={path(item.values)} fill="none" stroke={item.color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
                {item.values.map((value, index) => {
                  const previous = item.values[index - 1] ?? null;
                  const next = item.values[index + 1] ?? null;
                  const isolated = value !== null && previous === null && next === null;
                  const isEnd = value !== null && index === item.values.length - 1;
                  const isActive = value !== null && index === active;
                  if (value === null || !(isolated || isEnd || isActive)) return null;
                  return <circle key={index} cx={x(index)} cy={y(value)} r={4} fill={item.color} stroke="var(--surface)" strokeWidth={2} />;
                })}
              </g>
            ))}
          </svg>
        )}
        {active !== null && (
          <div className="pointer-events-none absolute top-1 z-10 w-40 rounded-md border border-line bg-surface px-2.5 py-1.5 shadow-sm" style={{ left: tooltipLeft }}>
            <p className="text-xs text-ink-3">{labels[active]}</p>
            {series.map((item) => (
              <p key={item.key} className="flex items-center gap-1.5 text-xs">
                <span aria-hidden className="inline-block h-0.5 w-3 rounded" style={{ background: item.color }} />
                <span className="font-semibold text-ink tabular">{item.values[active] === null ? "–" : format(item.values[active] as number)}</span>
                <span className="truncate text-ink-3">{item.label}</span>
              </p>
            ))}
          </div>
        )}
      </div>
      <button type="button" onClick={() => setShowTable((value) => !value)} className="mt-1 text-xs text-ink-3 hover:text-ink">
        {showTable ? "Hide table" : "Show as table"}
      </button>
      {showTable && (
        <div className="mt-2 max-h-56 overflow-auto">
          <Table>
            <thead>
              <tr>
                <Th>Period</Th>
                {series.map((item) => (
                  <Th key={item.key} align="right">
                    {item.label}
                  </Th>
                ))}
              </tr>
            </thead>
            <tbody>
              {labels.map((label, index) => (
                <tr key={label + index}>
                  <Td>{label}</Td>
                  {series.map((item) => (
                    <Td key={item.key} align="right">
                      {item.values[index] === null ? "–" : format(item.values[index] as number)}
                    </Td>
                  ))}
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; kind?: "line" | "dot" }[] }) {
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5 text-xs text-ink-2">
          {item.kind === "dot" ? (
            <span aria-hidden className="inline-block size-2.5 rounded-full" style={{ background: item.color }} />
          ) : (
            <span aria-hidden className="inline-block h-0.5 w-4 rounded" style={{ background: item.color }} />
          )}
          {item.label}
        </li>
      ))}
    </ul>
  );
}

export interface BarRow {
  key: string;
  label: string;
  value: number;
  display: string;
}

/** Ranked horizontal bars. One series, so every bar shares one color. */
export function BarList({ rows, max, empty }: { rows: BarRow[]; max?: number; empty?: ReactNode }) {
  const top = max ?? Math.max(1, ...rows.map((row) => row.value));
  if (!rows.length) return <>{empty}</>;
  return (
    <ul className="space-y-2">
      {rows.map((row) => (
        <li key={row.key} title={`${row.label}: ${row.display}`} className="group grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3">
          <span className="truncate text-xs text-ink-2">{row.label}</span>
          <span className="h-3 overflow-hidden" aria-hidden>
            <span
              className="block h-3 rounded-r transition-[filter] group-hover:brightness-110"
              style={{ width: `${Math.max(0.5, (row.value / top) * 100)}%`, background: "var(--series-1)" }}
            />
          </span>
          <span className="tabular text-xs font-medium text-ink">{row.display}</span>
        </li>
      ))}
    </ul>
  );
}

/** Before and after for each item, on one shared 0 to 100% scale. */
export function Dumbbell({ rows, baseLabel, candidateLabel }: { rows: { key: string; label: string; base: number; candidate: number }[]; baseLabel: string; candidateLabel: string }) {
  const id = useId();
  const base = "var(--dumbbell-base)";
  const candidate = "var(--dumbbell-candidate)";
  return (
    <div>
      <Legend
        items={[
          { label: baseLabel, color: base, kind: "dot" },
          { label: candidateLabel, color: candidate, kind: "dot" },
        ]}
      />
      <ul className="space-y-1">
        {rows.map((row) => {
          const left = Math.min(row.base, row.candidate) * 100;
          const span = Math.abs(row.candidate - row.base) * 100;
          const change = Math.round((row.candidate - row.base) * 100);
          return (
            <li key={row.key} className="grid grid-cols-[minmax(0,14rem)_1fr_3.5rem] items-center gap-3 py-0.5" title={`${row.label}: ${Math.round(row.base * 100)}% to ${Math.round(row.candidate * 100)}%`}>
              <span id={`${id}-${row.key}`} className="truncate text-xs text-ink-2">
                {row.label}
              </span>
              <span className="relative block h-4" role="img" aria-labelledby={`${id}-${row.key}`} aria-description={`${Math.round(row.base * 100)}% to ${Math.round(row.candidate * 100)}%`}>
                <span className="absolute inset-x-0 top-1/2 block h-px -translate-y-1/2 bg-line" />
                <span className="absolute top-1/2 block h-0.5 -translate-y-1/2 bg-line-strong" style={{ left: `${left}%`, width: `${span}%` }} />
                <Dot position={row.base} color={base} />
                <Dot position={row.candidate} color={candidate} />
              </span>
              <span className="tabular text-right text-xs font-medium text-ink">
                {change > 0 ? "+" : ""}
                {change} pts
              </span>
            </li>
          );
        })}
      </ul>
      <div className="mt-1 grid grid-cols-[minmax(0,14rem)_1fr_3.5rem] gap-3 text-xs text-ink-3">
        <span />
        <span className="flex justify-between tabular">
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </span>
        <span />
      </div>
    </div>
  );
}

function Dot({ position, color }: { position: number; color: string }) {
  return <span aria-hidden className="absolute top-1/2 block size-3 -translate-x-1/2 -translate-y-1/2 rounded-full" style={{ left: `${position * 100}%`, background: color, boxShadow: "0 0 0 2px var(--surface)" }} />;
}

export function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <p className="text-xs text-ink-3">{label}</p>
      <p className="mt-0.5 text-2xl font-semibold tracking-tight text-ink">{value}</p>
      {hint && <p className="mt-0.5 truncate text-xs text-ink-3">{hint}</p>}
    </div>
  );
}

export interface Part {
  label: string;
  value: number;
  color: string;
}

/** One whole split into parts. Each part keeps a 2px surface gap from the next. */
export function SegmentedBar({ parts, total, label }: { parts: Part[]; total: number; label: string }) {
  const visible = parts.filter((part) => part.value > 0);
  const description = visible.map((part) => `${part.label} ${part.value}`).join(", ");
  return (
    <div>
      <div role="img" aria-label={`${label}: ${description || "nothing yet"}`} className="flex h-2 gap-0.5 overflow-hidden rounded-full bg-track">
        {visible.map((part) => (
          <span key={part.label} title={`${part.label}: ${part.value}`} className="block h-2 transition-[width] duration-500" style={{ width: `${(part.value / Math.max(total, 1)) * 100}%`, background: part.color }} />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {parts.map((part) => (
          <li key={part.label} className="flex items-center gap-1.5 text-xs text-ink-2">
            <span aria-hidden className="inline-block size-2 rounded-sm" style={{ background: part.color }} />
            {part.label}
            <span className="tabular font-semibold text-ink">{part.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
