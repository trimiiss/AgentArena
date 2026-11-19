import clsx from "clsx";
import { AlertTriangle, CheckCircle2, CircleAlert, Clock, Loader2, MinusCircle, XCircle } from "lucide-react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white hover:brightness-110 border-transparent",
  secondary: "bg-surface text-ink border-line-strong hover:bg-raised",
  ghost: "bg-transparent text-ink-2 border-transparent hover:bg-raised",
  danger: "bg-surface text-critical-ink border-line-strong hover:bg-raised",
};

const BUTTON = "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  loading?: boolean;
}

export function Button({ variant = "secondary", loading, className, children, disabled, type = "button", ...rest }: ButtonProps) {
  return (
    <button type={type} disabled={disabled || loading} className={clsx(BUTTON, VARIANTS[variant], className)} {...rest}>
      {loading && <Loader2 size={14} className="animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function LinkButton({ to, variant = "secondary", children }: { to: string; variant?: Variant; children: ReactNode }) {
  return (
    <Link to={to} className={clsx(BUTTON, VARIANTS[variant])}>
      {children}
    </Link>
  );
}

export function Card({ title, subtitle, action, children, className }: { title?: string; subtitle?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx("rounded-lg border border-line bg-surface", className)}>
      {(title || action) && (
        <header className="flex items-start justify-between gap-4 px-4 pt-3.5 pb-1">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="text-xs text-ink-3">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, description, actions, back }: { title: string; description?: ReactNode; actions?: ReactNode; back?: { to: string; label: string } }) {
  return (
    <div className="mb-5">
      {back && (
        <Link to={back.to} className="mb-1 inline-block text-xs text-ink-3 hover:text-ink">
          ← {back.label}
        </Link>
      )}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
          {description && <p className="mt-0.5 text-sm text-ink-2">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}

const FIELD = "w-full rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-sm text-ink placeholder:text-ink-3 disabled:opacity-60";

export function Field({ label, hint, error, children }: { label: string; hint?: ReactNode; error?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-ink-2">{label}</span>
      {children}
      {hint && !error && <span className="mt-1 block text-xs text-ink-3">{hint}</span>}
      {error && <span className="mt-1 block text-xs text-critical-ink">{error}</span>}
    </label>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx(FIELD, props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={clsx(FIELD, props.className)} />;
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={clsx(FIELD, props.className)} />;
}

type Tone = "good" | "warning" | "serious" | "critical" | "info" | "neutral";

const TONE_DOT: Record<Tone, string> = {
  good: "text-good",
  warning: "text-warning",
  serious: "text-serious",
  critical: "text-critical",
  info: "text-accent",
  neutral: "text-ink-3",
};

const TONE_ICON: Record<Tone, typeof CheckCircle2> = {
  good: CheckCircle2,
  warning: AlertTriangle,
  serious: CircleAlert,
  critical: XCircle,
  info: Clock,
  neutral: MinusCircle,
};

/** Status always pairs an icon with a word, so it never relies on color alone. */
export function StatusBadge({ tone, label, spin }: { tone: Tone; label: string; spin?: boolean }) {
  const Icon = spin ? Loader2 : TONE_ICON[tone];
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-line bg-raised px-2 py-0.5 text-xs font-medium text-ink-2">
      <Icon size={12} className={clsx(TONE_DOT[tone], spin && "animate-spin")} aria-hidden />
      {label}
    </span>
  );
}

const RUN_TONES: Record<string, { tone: Tone; spin?: boolean }> = {
  queued: { tone: "neutral" },
  running: { tone: "info", spin: true },
  evaluating: { tone: "info", spin: true },
  completed: { tone: "good" },
  failed: { tone: "critical" },
  cancelling: { tone: "warning" },
  cancelled: { tone: "neutral" },
  pending: { tone: "neutral" },
  dispatched: { tone: "info" },
  retry_wait: { tone: "warning" },
  timed_out: { tone: "serious" },
  active: { tone: "good" },
  unreachable: { tone: "critical" },
  draft: { tone: "neutral" },
  disabled: { tone: "neutral" },
};

export function StateBadge({ state }: { state: string }) {
  const entry = RUN_TONES[state] ?? { tone: "neutral" as Tone };
  return <StatusBadge tone={entry.tone} spin={entry.spin} label={state.replaceAll("_", " ")} />;
}

export function PassBadge({ passed }: { passed: boolean | null | undefined }) {
  if (passed === null || passed === undefined) return <StatusBadge tone="neutral" label="not scored" />;
  return passed ? <StatusBadge tone="good" label="passed" /> : <StatusBadge tone="critical" label="failed" />;
}

export function Tag({ children }: { children: ReactNode }) {
  return <span className="inline-block whitespace-nowrap rounded bg-raised px-1.5 py-0.5 text-xs text-ink-2">{children}</span>;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-10 text-sm text-ink-3 justify-center">
      <Loader2 size={16} className="animate-spin" aria-hidden />
      {label}
    </div>
  );
}

export function ErrorNotice({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : "Something went wrong";
  const correlationId = error instanceof ApiError ? error.correlationId : null;
  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-line-strong bg-raised px-3 py-2 text-sm text-ink">
      <XCircle size={16} className="mt-0.5 shrink-0 text-critical" aria-hidden />
      <div className="min-w-0">
        <p className="break-words">{message}</p>
        {correlationId && <p className="font-mono text-xs text-ink-3">Reference: {correlationId}</p>}
      </div>
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children && <p className="max-w-md text-sm text-ink-3">{children}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  );
}

export function Th({ children, align }: { children?: ReactNode; align?: "right" }) {
  return (
    <th scope="col" className={clsx("whitespace-nowrap border-b border-line px-3 py-2 text-xs font-medium text-ink-3", align === "right" && "text-right")}>
      {children}
    </th>
  );
}

export function Td({ children, align, mono, className }: { children?: ReactNode; align?: "right"; mono?: boolean; className?: string }) {
  return <td className={clsx("border-b border-line px-3 py-2 align-middle text-ink", align === "right" && "tabular whitespace-nowrap text-right", mono && "font-mono text-xs", className)}>{children}</td>;
}

export function KeyValues({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
      {items.map(([key, value]) => (
        <div key={key} className="flex items-baseline justify-between gap-3 border-b border-line py-1 sm:block sm:border-0 sm:py-0">
          <dt className="text-xs text-ink-3">{key}</dt>
          <dd className="min-w-0 break-words text-sm text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (value: T) => void; label: string }) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-md border border-line-strong bg-surface p-0.5">
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={clsx("rounded px-2.5 py-1 text-xs font-medium transition", value === option.value ? "bg-accent-wash text-accent-ink" : "text-ink-2 hover:bg-raised")}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
