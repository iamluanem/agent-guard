"use client";

import Link from "next/link";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { humanize, money, short, time } from "@/lib/format";
import type { TransactionDetail } from "@/lib/types";
import { Button, Drawer, Notice, Section, StateChip, StateRail, Violations } from "./ui";

export function TransactionDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data: tx, error, reload } = useApi<TransactionDetail>(id ? `/v1/transactions/${id}` : null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function decide(kind: "approve" | "reject") {
    if (!tx?.customer_id) return;
    setBusy(true);
    setActionError(null);
    try {
      await api(`/v1/transactions/${tx.id}/${kind}`, { method: "POST", body: { customer_id: tx.customer_id } });
      await reload();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const show = tx && tx.id === id;
  return (
    <Drawer
      open={!!id}
      onClose={onClose}
      title={
        <>
          <div className="id text-muted">{id}</div>
          <div className="mt-0.5 text-lg font-semibold">{show ? `${tx.product_name} · ${money(tx.amount_cents, tx.currency)}` : "Loading…"}</div>
        </>
      }
    >
      {error && <Notice>{error}</Notice>}
      {show && (
        <>
          <StateRail state={tx.state} history={tx.history.map((h) => h.to_state)} />

          {tx.state === "AWAITING_APPROVAL" && (
            <div className="mt-5 rounded-md bg-pending-soft px-4 py-3">
              <p className="text-sm text-pending">
                The agent wants to spend more than the customer&apos;s budget. Approving re-checks every other rule before the payment runs.
              </p>
              <div className="mt-3 flex gap-2">
                <Button variant="primary" disabled={busy} onClick={() => decide("approve")}>Approve purchase</Button>
                <Button variant="danger" disabled={busy} onClick={() => decide("reject")}>Reject purchase</Button>
              </div>
              {actionError && <p className="mt-2 text-sm text-danger">{actionError}</p>}
            </div>
          )}

          {tx.incident_id && (
            <p className="mt-5 text-sm">
              An execution mismatch opened an incident.{" "}
              <Link className="text-recovered underline" href={`/incidents?open=${tx.incident_id}`}>View incident {short(tx.incident_id)}</Link>
            </p>
          )}

          <Section title="Policy decision">
            <Violations items={tx.violations} />
          </Section>

          <Section title="Purchase">
            <dl className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-muted">Merchant</dt><dd className="id">{tx.merchant_id}</dd>
              <dt className="text-muted">Product</dt><dd>{tx.product_name} <span className="text-muted">({tx.product_category})</span></dd>
              <dt className="text-muted">Attributes</dt><dd>{Object.entries(tx.attributes).map(([k, v]) => `${k} ${v}`).join(", ") || "—"}</dd>
              <dt className="text-muted">Quantity</dt><dd>{tx.quantity} × {money(tx.unit_amount_cents, tx.currency)}</dd>
              <dt className="text-muted">Authorized</dt><dd>{money(tx.amount_cents, tx.currency)}</dd>
              <dt className="text-muted">Captured</dt><dd>{money(tx.captured_amount_cents, tx.currency)}</dd>
              <dt className="text-muted">Mandate</dt><dd className="id">{tx.mandate_id ? `${tx.mandate_id} v${tx.mandate_version}` : "none"}</dd>
              <dt className="text-muted">Execution slot</dt><dd>{tx.execution_slot ?? "not claimed"}</dd>
              <dt className="text-muted">Idempotency key</dt><dd className="id break-all">{tx.idempotency_key}</dd>
              <dt className="text-muted">Payment ref</dt><dd className="id">{tx.payment_ref ?? "—"}</dd>
              <dt className="text-muted">Correlation</dt>
              <dd><Link className="id text-recovered underline" href={`/audit?correlation_id=${tx.correlation_id}`}>{tx.correlation_id}</Link></dd>
            </dl>
          </Section>

          <Section title="State history">
            <ol className="border-l border-rule-strong pl-4 text-sm">
              {tx.history.map((h) => (
                <li key={h.id} className="relative pb-3 last:pb-0">
                  <span className="absolute -left-[21px] top-1.5 size-2.5 rounded-full border-2 border-surface bg-ink" aria-hidden />
                  <div className="flex flex-wrap items-center gap-2"><StateChip state={h.to_state} /><span className="text-xs text-muted">{time(h.at)}</span></div>
                  <div className="mt-0.5 text-muted">{h.reason}</div>
                </li>
              ))}
            </ol>
          </Section>

          <Section title="Provider webhooks" aside={<span className="text-xs text-muted">simulated providers</span>}>
            {tx.provider_events.length === 0 ? (
              <p className="text-sm text-muted">No webhooks. The payment provider was never called.</p>
            ) : (
              <ul className="divide-y divide-rule border-y border-rule text-sm">
                {tx.provider_events.map((e) => (
                  <li key={e.id} className="flex items-center justify-between gap-3 py-2">
                    <span>{humanize(e.type)} <span className="text-muted">· {e.source} #{e.sequence}</span></span>
                    <StateChip state={e.status} />
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </>
      )}
    </Drawer>
  );
}
