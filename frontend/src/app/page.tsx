"use client";

import { useEffect, useState } from "react";
import { TransactionDrawer } from "@/components/transaction-drawer";
import { Button, Notice, StateChip } from "@/components/ui";
import { api, onLiveEvent, useApi, type LiveEvent } from "@/lib/api";
import { ago, humanize, money, short } from "@/lib/format";
import type { Overview, ScenarioResult } from "@/lib/types";

const SCENARIOS = [
  { n: 1, title: "Correct purchase", expect: "Approved, paid and verified against the mandate." },
  { n: 2, title: "Duplicate purchase", expect: "The agent retries; the second execution is blocked." },
  { n: 3, title: "Wrong quantity", expect: "Two pairs instead of one. Rejected before payment." },
  { n: 4, title: "Excessive amount", expect: "$179 against a $150 budget. Waits for the customer." },
  { n: 5, title: "Incorrect completed purchase", expect: "Merchant ships two. Incident opened, refund recovers the money." },
  { n: 6, title: "Merchant refund fails", expect: "Wrong size, refund declined. A protection claim pays out (simulated)." },
];

export default function OverviewPage() {
  const { data: m, error } = useApi<Overview>("/v1/metrics/overview");
  const [openTx, setOpenTx] = useState<string | null>(null);

  return (
    <>
      <header className="mb-10 max-w-[64ch]">
        <h1 className="text-[2.5rem] font-semibold leading-[1.1] tracking-[-0.025em]">
          Where agent mistakes would have cost money
        </h1>
        <p className="mt-3 text-[1rem] leading-relaxed text-muted">
          Every purchase an AI agent attempts is checked against the customer&apos;s signed mandate. Losses are stopped
          before payment, recovered from the merchant, or covered by a simulated protection payout. Each is counted separately.
        </p>
      </header>

      {error && <Notice>{error}</Notice>}
      {m && <MoneyFlow m={m} />}

      <div className="mt-14 grid gap-12 lg:grid-cols-[1fr_340px]">
        <Scenarios onOpen={setOpenTx} />
        <LiveFeed onOpen={setOpenTx} />
      </div>
      <TransactionDrawer id={openTx} onClose={() => setOpenTx(null)} />
    </>
  );
}

function MoneyFlow({ m }: { m: Overview }) {
  const parts = [
    { key: "prevented", label: "Prevented before payment", cents: m.prevented_loss_cents, bar: "bg-prevented", text: "text-prevented",
      note: `${m.blocked_attempts} blocked attempts` },
    { key: "recovered", label: "Recovered from merchants", cents: m.recovered_cents, bar: "bg-recovered", text: "text-recovered",
      note: "confirmed merchant refunds" },
    { key: "payout", label: "Simulated protection payouts", cents: m.simulated_payouts_cents, bar: "hatch", text: "text-payout",
      note: "sandbox ledger, not real insurance" },
  ];
  const total = parts.reduce((a, p) => a + p.cents, 0) || 1;
  return (
    <section aria-labelledby="flow">
      <h2 id="flow" className="sr-only">Money protected</h2>
      <div className="flex h-5 w-full overflow-hidden rounded-sm bg-rule" role="img"
        aria-label={parts.map((p) => `${p.label} ${money(p.cents)}`).join(", ")}>
        {parts.map((p, i) => (
          <div key={p.key} className={`bar-in h-full ${p.bar}`}
            style={{ width: `${(p.cents / total) * 100}%`, animationDelay: `${i * 120}ms` }} />
        ))}
      </div>
      <dl className="mt-5 grid gap-6 sm:grid-cols-3">
        {parts.map((p) => (
          <div key={p.key} className="border-l-2 pl-4" style={{ borderColor: `var(--color-${p.key})` }}>
            <dt className="text-sm text-muted">{p.label}</dt>
            <dd className={`mt-1 text-[2rem] font-semibold leading-none tracking-[-0.02em] ${p.text}`}>{money(p.cents)}</dd>
            <dd className="mt-1.5 text-xs text-muted">{p.note}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-6 flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted">
        <span><span className="font-semibold text-ink">{m.transactions_monitored}</span> transactions monitored</span>
        <span><span className="font-semibold text-ink">{m.incidents_detected}</span> incidents detected, {m.incidents_open} open</span>
        <span><span className="font-semibold text-ink">{m.awaiting_approval}</span> waiting for customer approval</span>
        <span><span className="font-semibold text-ink">{m.claims_in_review}</span> claims in review</span>
      </p>
    </section>
  );
}

function Scenarios({ onOpen }: { onOpen: (id: string) => void }) {
  const [running, setRunning] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, ScenarioResult | string>>({});

  async function run(n: number) {
    setRunning(n);
    try {
      const r = await api<ScenarioResult>(`/v1/demo/scenarios/${n}`, { method: "POST" });
      setResults((s) => ({ ...s, [n]: r }));
    } catch (e) {
      setResults((s) => ({ ...s, [n]: (e as Error).message }));
    } finally {
      setRunning(null);
    }
  }

  return (
    <section aria-labelledby="scen">
      <h2 id="scen" className="text-xl font-semibold tracking-tight">Demo scenarios</h2>
      <p className="mt-1 text-sm text-muted">
        Each run creates a new customer and mandate (&ldquo;one pair of running shoes, size 38, up to $150&rdquo;) and drives the real engine end to end.
      </p>
      <ol className="mt-4 divide-y divide-rule border-y border-rule">
        {SCENARIOS.map((s) => {
          const r = results[s.n];
          return (
            <li key={s.n} className="grid grid-cols-[2rem_1fr_auto] items-start gap-3 py-4">
              <span className="pt-0.5 text-sm font-semibold text-faint">{s.n}</span>
              <div className="min-w-0">
                <div className="font-medium">{s.title}</div>
                <div className="text-sm text-muted">{s.expect}</div>
                {typeof r === "string" && <p className="mt-2 text-sm text-danger">{r}</p>}
                {r && typeof r !== "string" && (
                  <div className="flash mt-2 flex flex-wrap items-center gap-2 rounded text-sm">
                    {r.transaction_ids.map((t) => (
                      <button key={t} onClick={() => onOpen(t)} className="flex items-center gap-1.5 rounded px-1 hover:bg-paper">
                        <span className="id text-recovered underline">{short(t)}</span>
                        <StateChip state={r.final_states[t]} />
                      </button>
                    ))}
                    <span className="text-xs text-muted">{r.steps.length} audited steps</span>
                  </div>
                )}
              </div>
              <Button onClick={() => run(s.n)} disabled={running !== null} aria-label={`Run scenario ${s.n}: ${s.title}`}>
                {running === s.n ? "Running…" : "Run"}
              </Button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

const FEED_TYPES = new Set(["transaction.completed", "transaction.blocked", "transaction.awaiting_approval",
  "incident.detected", "incident.recovered", "incident.escalated", "claim.paid", "claim.escalated_to_review",
  "claim.rejected", "mandate.created", "mandate.revoked", "transaction.refunded"]);

function LiveFeed({ onOpen }: { onOpen: (id: string) => void }) {
  const [items, setItems] = useState<LiveEvent[]>([]);
  const [, tick] = useState(0);
  useEffect(() => {
    const off = onLiveEvent((e) => FEED_TYPES.has(e.type) && setItems((xs) => [e, ...xs].slice(0, 14)));
    const t = setInterval(() => tick((x) => x + 1), 5000);
    return () => { off(); clearInterval(t); };
  }, []);
  return (
    <section aria-labelledby="feed" aria-live="polite">
      <h2 id="feed" className="text-xl font-semibold tracking-tight">Live activity</h2>
      <p className="mt-1 text-sm text-muted">Streamed from the event outbox as it happens.</p>
      {items.length === 0 ? (
        <p className="mt-4 border-y border-rule py-8 text-center text-sm text-muted">Run a scenario to see events arrive.</p>
      ) : (
        <ul className="mt-4 divide-y divide-rule border-y border-rule">
          {items.map((e) => (
            <li key={e.id} className="flash py-2.5 text-sm">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-medium">{humanize(e.type)}</span>
                <span className="shrink-0 text-xs text-muted">{ago(e.created_at)}</span>
              </div>
              {e.entity_id.startsWith("txn_") ? (
                <button onClick={() => onOpen(e.entity_id)} className="id text-recovered underline">{short(e.entity_id)}</button>
              ) : (
                <span className="id text-muted">{short(e.entity_id)}</span>
              )}
              {typeof e.payload.amount_cents === "number" && (
                <span className="ml-2 text-muted">{money(e.payload.amount_cents as number)}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
