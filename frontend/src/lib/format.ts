export const EMPTY = "–";

export function percent(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? EMPTY : `${(value * 100).toFixed(digits)}%`;
}

export function milliseconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return EMPTY;
  if (value >= 10_000) return `${(value / 1000).toFixed(1)} s`;
  return `${Math.round(value).toLocaleString("en-US")} ms`;
}

export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined) return EMPTY;
  return new Intl.NumberFormat("en-US", { notation: value >= 10_000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(value);
}

export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Not reported";
  if (value === 0) return "$0";
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

export function score(value: number | null | undefined): string {
  return value === null || value === undefined ? EMPTY : value.toFixed(2);
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return EMPTY;
  return new Date(value).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export function relative(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86_400)} d ago`;
}

export function duration(start: string | null | undefined, end: string | null | undefined): string {
  if (!start) return EMPTY;
  const stop = end ? new Date(end).getTime() : Date.now();
  const seconds = Math.max(0, (stop - new Date(start).getTime()) / 1000);
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

export function words(value: string): string {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function shortId(value: string): string {
  return value.slice(0, 8);
}
