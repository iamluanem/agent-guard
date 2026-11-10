"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useApi } from "@/lib/api";
import type { Overview } from "@/lib/types";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/transactions", label: "Live transactions" },
  { href: "/mandates", label: "Protection policies" },
  { href: "/incidents", label: "Incidents" },
  { href: "/recovery", label: "Recovery center" },
  { href: "/claims", label: "Claims" },
  { href: "/audit", label: "Audit trail" },
];

export function Sidebar() {
  const path = usePathname();
  const { data, error } = useApi<Overview>("/v1/metrics/overview");
  const badges: Record<string, number | undefined> = {
    "/transactions": data?.awaiting_approval,
    "/incidents": data?.incidents_open,
    "/claims": data?.claims_in_review,
  };
  return (
    <aside className="border-b border-rule bg-surface md:sticky md:top-0 md:h-screen md:border-b-0 md:border-r">
      <div className="flex items-center gap-2.5 px-5 pb-4 pt-6">
        <Shield />
        <div>
          <div className="text-[1.0625rem] font-semibold leading-tight tracking-tight">AgentGuard</div>
          <div className="text-xs text-muted">Sandbox environment</div>
        </div>
      </div>
      <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:overflow-visible">
        {NAV.map((n) => {
          const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
          const badge = badges[n.href];
          return (
            <Link
              key={n.href}
              href={n.href}
              aria-current={active ? "page" : undefined}
              className={`flex shrink-0 items-center justify-between rounded-md px-3 py-2 text-[0.9375rem] ${
                active ? "bg-ink text-white" : "text-ink hover:bg-paper"
              }`}
            >
              {n.label}
              {badge ? (
                <span className={`ml-3 rounded px-1.5 text-xs font-semibold ${active ? "bg-white/20" : "bg-pending-soft text-pending"}`}>
                  {badge}
                </span>
              ) : null}
            </Link>
          );
        })}
      </nav>
      <p className="hidden px-5 pt-6 text-xs leading-relaxed text-muted md:block">
        {error ? <span className="text-danger">{error}</span> : "Payments, merchants and payouts here are simulated. No real money moves."}
      </p>
    </aside>
  );
}

function Shield() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden>
      <path d="M14 2 4 6v7c0 6.2 4.2 11 10 13 5.8-2 10-6.8 10-13V6L14 2Z" fill="var(--color-ink)" />
      <path d="m9.5 14.2 3.2 3.2 6-6.4" stroke="white" strokeWidth="2.2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
