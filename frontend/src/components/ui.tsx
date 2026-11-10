"use client";

import { useEffect, useRef } from "react";
import { humanize, money } from "@/lib/format";
import type { TxState, Violation } from "@/lib/types";

export function PageHeader({ title, lede, actions }: { title: string; lede: string; actions?: React.ReactNode }) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-[62ch]">
        <h1 className="text-[2rem] font-semibold leading-tight tracking-[-0.02em]">{title}</h1>
        <p className="mt-2 text-[0.9375rem] leading-relaxed text-muted">{lede}</p>
      </div>
      {actions ? <div className="flex gap-2">{actions}</div> : null}
    </header>
  );
}

type Tone = "neutral" | "prevented" | "recovered" | "payout" | "danger" | "pending" | "ok";
const TONES: Record<Tone, string> = {
  neutral: "bg-paper text-muted ring-rule",
  ok: "bg-surface text-ink ring-rule-strong",
  prevented: "bg-prevented-soft text-prevented ring-prevented/25",
  recovered: "bg-recovered-soft text-recovered ring-recovered/25",
  payout: "bg-payout-soft text-payout ring-payout/30",
  danger: "bg-danger-soft text-danger ring-danger/25",
  pending: "bg-pending-soft text-pending ring-pending/25",
};

export function Chip({ tone = "neutral", children }: { tone?: Tone; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {children}
    </span>
  );
}

const STATE_TONE: Record<string, Tone> = {
  COMPLETED: "ok", AUTHORIZED: "ok", EXECUTING: "ok", CREATED: "neutral",
  BLOCKED: "prevented", AWAITING_APPROVAL: "pending", FAILED: "danger", DISPUTED: "danger",
  RECOVERY_PENDING: "danger", REFUNDED: "recovered",
  OPEN: "danger", RECOVERING: "pending", RECOVERED: "recovered", ESCALATED: "payout", CLOSED: "neutral",
  PAID: "payout", APPROVED: "payout", IN_REVIEW: "pending", REJECTED: "neutral", SUBMITTED: "neutral",
  REQUESTED: "pending", SUCCEEDED: "recovered", RETRY_SCHEDULED: "pending", PENDING: "neutral",
  ACTIVE: "ok", REVOKED: "neutral",
};

export function StateChip({ state }: { state: string }) {
  return <Chip tone={STATE_TONE[state] ?? "neutral"}>{humanize(state)}</Chip>;
}

export function Button({
  children, variant = "secondary", ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "quiet" }) {
  const v = {
    primary: "bg-ink text-white hover:bg-ink/90",
    secondary: "bg-surface text-ink ring-1 ring-inset ring-rule-strong hover:bg-paper",
    danger: "bg-surface text-danger ring-1 ring-inset ring-danger/40 hover:bg-danger-soft",
    quiet: "text-recovered hover:underline px-0",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${v} ${props.className ?? ""}`}
    >
      {children}
    </button>
  );
}

export function Notice({ tone = "danger", children }: { tone?: "danger" | "neutral"; children: React.ReactNode }) {
  return (
    <div role={tone === "danger" ? "alert" : "status"}
      className={`rounded-md px-4 py-3 text-sm ${tone === "danger" ? "bg-danger-soft text-danger" : "bg-surface text-muted ring-1 ring-rule"}`}>
      {children}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="border-y border-rule py-12 text-center text-sm text-muted">{children}</div>;
}

export function Table({ head, children }: { head: React.ReactNode[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto border-y border-rule bg-surface">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-rule text-xs text-muted">
            {head.map((h, i) => (
              <th key={i} scope="col" className="whitespace-nowrap px-4 py-2.5 font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-rule">{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-4 py-3 align-top ${className}`}>{children}</td>;
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: React.ReactNode; children: React.ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className="m-0 ml-auto h-full max-h-none w-full max-w-[640px] bg-surface p-0 text-ink shadow-[-12px_0_40px_rgba(24,33,43,0.12)] backdrop:bg-ink/30"
    >
      <div className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-rule bg-surface px-6 py-4">
        <div className="min-w-0">{title}</div>
        <Button variant="secondary" onClick={onClose} aria-label="Close panel">Close</Button>
      </div>
      <div className="px-6 py-5">{open ? children : null}</div>
    </dialog>
  );
}

export function Section({ title, children, aside }: { title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="mt-6 first:mt-0">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function Violations({ items }: { items: Violation[] }) {
  if (!items.length) return <p className="text-sm text-muted">All policy rules passed.</p>;
  return (
    <ul className="divide-y divide-rule border-y border-rule">
      {items.map((v, i) => (
        <li key={i} className="flex gap-3 py-2.5 text-sm">
          <span className={`mt-1.5 size-2 shrink-0 rounded-full ${v.severity === "BLOCK" ? "bg-prevented" : "bg-pending"}`} aria-hidden />
          <div>
            <div className="font-medium">{humanize(v.code)} <span className="font-normal text-muted">· {v.severity === "BLOCK" ? "blocks" : "needs approval"}</span></div>
            <div className="text-muted">{v.message}</div>
            {(v.expected || v.actual) && (
              <div className="mt-1 text-xs text-muted">
                Allowed <span className="text-ink">{val(v.code, v.expected)}</span>, attempted <span className="text-ink">{val(v.code, v.actual)}</span>
              </div>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

const MONEY_CODES = new Set(["BUDGET_EXCEEDED", "BUDGET_HARD_LIMIT", "AMOUNT_INCONSISTENT", "OVERCHARGE"]);
function val(code: string, v: string | null) {
  if (v === null) return "—";
  return MONEY_CODES.has(code) && /^\d+$/.test(v) ? money(Number(v)) : v;
}

const MAIN: TxState[] = ["CREATED", "AUTHORIZED", "EXECUTING", "COMPLETED"];
const RECOVERY: TxState[] = ["DISPUTED", "RECOVERY_PENDING", "REFUNDED"];

/** The transaction's position on the state machine: happy path, plus the recovery branch when entered. */
export function StateRail({ state, history }: { state: TxState; history: string[] }) {
  const seen = new Set(history);
  const branch = seen.has("DISPUTED");
  const exit = ["BLOCKED", "FAILED", "AWAITING_APPROVAL"].includes(state) ? state : null;
  const path: string[] = exit ? MAIN.filter((s) => seen.has(s)) : [...MAIN, ...(branch ? RECOVERY : [])];
  return (
    <ol className="flex flex-wrap items-center gap-y-2 text-xs" aria-label="State history">
      {path.map((s, i) => {
        const reached = seen.has(s);
        const current = s === state;
        return (
          <li key={s} className="flex items-center">
            {i > 0 && <span className={`mx-1 h-px w-5 ${reached ? "bg-ink" : "bg-rule-strong"}`} aria-hidden />}
            <span
              aria-current={current ? "step" : undefined}
              className={`rounded px-1.5 py-0.5 ${current ? "bg-ink text-white" : reached ? "text-ink ring-1 ring-inset ring-ink/40" : "text-faint ring-1 ring-inset ring-rule"}`}
            >
              {humanize(s)}
            </span>
          </li>
        );
      })}
      {exit && (
        <li className="flex items-center">
          <span className="mx-1 h-px w-5 bg-ink" aria-hidden />
          <StateChip state={exit} />
        </li>
      )}
    </ol>
  );
}
