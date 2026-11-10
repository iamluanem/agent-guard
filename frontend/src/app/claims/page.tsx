"use client";

import Link from "next/link";
import { useState } from "react";
import { Button, Chip, Drawer, Empty, Notice, PageHeader, Section, StateChip, Table, Td } from "@/components/ui";
import { api, useApi, useQueryParam } from "@/lib/api";
import { ago, humanize, money, short, time } from "@/lib/format";
import type { Claim, LedgerEntry, Policy } from "@/lib/types";

export default function ClaimsPage() {
  const { data, error } = useApi<Claim[]>("/v1/claims");
  const policies = useApi<Policy[]>("/v1/policies/protection", { live: false });
  const ledger = useApi<{ sandbox: boolean; balanced: boolean; entries: LedgerEntry[] }>("/v1/ledger?limit=40");
  const initial = useQueryParam("open");
  const [picked, setOpen] = useState<string | null | undefined>(undefined);
  const open = picked === undefined ? initial : picked;
  const p = policies.data?.[0];

  return (
    <>
      <PageHeader
        title="Claims"
        lede="When a merchant won't refund, the incident becomes a claim. Eligibility and amounts are decided by fixed rules; larger claims wait for a reviewer."
      />
      <div className="mb-8 flex gap-3 rounded-md bg-payout-soft px-4 py-3 text-sm text-payout">
        <span className="hatch mt-0.5 h-4 w-6 shrink-0 rounded-sm" aria-hidden />
        <p>Payouts are simulated entries in a sandbox ledger. This MVP does not provide real insurance coverage or move real money.</p>
      </div>
      {error && <Notice>{error}</Notice>}

      <div className="grid gap-12 lg:grid-cols-[1fr_300px]">
        <div>
          {data?.length === 0 && <Empty>No claims yet. Scenario 6 creates one.</Empty>}
          {data && data.length > 0 && (
            <Table head={["Claim", "Requested", "Payout", "Decided by", "Status", "Opened"]}>
              {data.map((c) => (
                <tr key={c.id} className="cursor-pointer hover:bg-paper" onClick={() => setOpen(c.id)}>
                  <Td><button className="id text-recovered underline">{short(c.id)}</button></Td>
                  <Td className="whitespace-nowrap">{money(c.requested_amount_cents, c.currency)}</Td>
                  <Td className="whitespace-nowrap">{money(c.approved_amount_cents, c.currency)}</Td>
                  <Td className="text-muted">{c.decided_by ?? (c.status === "IN_REVIEW" ? "Waiting for reviewer" : "—")}</Td>
                  <Td><StateChip state={c.status} /></Td>
                  <Td className="whitespace-nowrap text-muted">{ago(c.created_at)}</Td>
                </tr>
              ))}
            </Table>
          )}

          <h2 className="mt-12 text-xl font-semibold tracking-tight">Sandbox ledger</h2>
          <p className="mt-1 text-sm text-muted">
            Double-entry: every movement debits one account and credits another.{" "}
            {ledger.data && (ledger.data.balanced ? "Debits and credits balance." : <span className="text-danger">Ledger is out of balance.</span>)}
          </p>
          {ledger.data && (
            <div className="mt-3">
              <Table head={["Journal", "Account", "Debit", "Credit", "Kind"]}>
                {ledger.data.entries.map((e) => (
                  <tr key={e.id}>
                    <Td className="id text-muted">{short(e.journal_id)}</Td>
                    <Td className="id">{e.account}</Td>
                    <Td className="text-right">{e.direction === "DEBIT" ? money(e.amount_cents, e.currency) : ""}</Td>
                    <Td className="text-right">{e.direction === "CREDIT" ? money(e.amount_cents, e.currency) : ""}</Td>
                    <Td>{e.kind === "MERCHANT_REFUND" ? <Chip tone="recovered">Merchant refund</Chip> : <Chip tone="payout">Simulated payout</Chip>}</Td>
                  </tr>
                ))}
              </Table>
            </div>
          )}
        </div>

        {p && (
          <aside>
            <h2 className="text-base font-semibold">{p.name}</h2>
            <p className="text-xs text-muted">{p.code}, version {p.version}</p>
            <dl className="mt-4 space-y-3 text-sm">
              <div><dt className="text-muted">Deductible</dt><dd>{money(p.deductible_cents)}</dd></div>
              <div><dt className="text-muted">Limit per claim</dt><dd>{money(p.per_claim_limit_cents)}</dd></div>
              <div><dt className="text-muted">Limit per customer</dt><dd>{money(p.aggregate_limit_cents)}</dd></div>
              <div><dt className="text-muted">Approved automatically up to</dt><dd>{money(p.auto_approve_limit_cents)}</dd></div>
              <div><dt className="text-muted">Covers</dt><dd>{p.covered_incident_types.map(humanize).join(", ")}</dd></div>
              <div><dt className="text-muted">Excludes</dt><dd>{p.exclusions.map(humanize).join(", ")}</dd></div>
            </dl>
          </aside>
        )}
      </div>
      <ClaimDrawer id={open} onClose={() => setOpen(null)} />
    </>
  );
}

function ClaimDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data: c, reload } = useApi<Claim>(id ? `/v1/claims/${id}` : null);
  const [reviewer, setReviewer] = useState("ops@agentguard.example");
  const [note, setNote] = useState("");
  const [amount, setAmount] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const show = c && c.id === id;

  async function review(approve: boolean) {
    setBusy(true);
    setErr(null);
    try {
      await api(`/v1/claims/${id}/review`, { method: "POST", body: {
        approve, reviewer, note, amount_cents: amount ? Math.round(Number(amount) * 100) : null } });
      await reload();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-md border border-rule-strong bg-surface px-3 py-1.5 text-sm";
  return (
    <Drawer open={!!id} onClose={onClose}
      title={<><div className="id text-muted">{id}</div><div className="mt-0.5 text-lg font-semibold">{show ? `Claim for ${money(c.requested_amount_cents, c.currency)}` : "Loading…"}</div></>}>
      {show && (
        <>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <StateChip state={c.status} />
            <Link className="id text-recovered underline" href={`/incidents?open=${c.incident_id}`}>{short(c.incident_id)}</Link>
            {c.decision_reason && <span className="text-muted">{c.decision_reason}</span>}
          </div>

          <Section title="Eligibility checks">
            <ul className="divide-y divide-rule border-y border-rule text-sm">
              {c.checks.map((ch) => (
                <li key={ch.code} className="flex gap-3 py-2.5">
                  <span aria-hidden className={`mt-0.5 font-semibold ${ch.passed ? "text-prevented" : ch.hard ? "text-danger" : "text-pending"}`}>
                    {ch.passed ? "✓" : ch.hard ? "✕" : "!"}
                  </span>
                  <div>
                    <div className="font-medium">{humanize(ch.code)} <span className="sr-only">{ch.passed ? "passed" : "failed"}</span>
                      {!ch.hard && <span className="font-normal text-muted"> · routes to review</span>}</div>
                    <div className="text-muted">{ch.detail}</div>
                  </div>
                </li>
              ))}
            </ul>
          </Section>

          <Section title="Amounts">
            <dl className="grid grid-cols-[160px_1fr] gap-y-1.5 text-sm">
              <dt className="text-muted">Disputed</dt><dd>{money(c.requested_amount_cents, c.currency)}</dd>
              <dt className="text-muted">Deductible</dt><dd>− {money(c.deductible_applied_cents, c.currency)}</dd>
              <dt className="text-muted">{c.status === "IN_REVIEW" ? "Maximum payable" : "Payout"}</dt>
              <dd className="font-semibold text-payout">{money(c.approved_amount_cents, c.currency)} <span className="font-normal text-muted">(simulated)</span></dd>
              {c.paid_at && (<><dt className="text-muted">Paid</dt><dd>{time(c.paid_at)}</dd></>)}
              {c.reviewer_note && (<><dt className="text-muted">Reviewer note</dt><dd>{c.reviewer_note}</dd></>)}
            </dl>
          </Section>

          {c.status === "IN_REVIEW" && (
            <Section title="Review">
              <p className="mb-3 text-sm text-muted">You can lower the payout, but not raise it above the policy maximum.</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-sm">Reviewer<input className={field} value={reviewer} onChange={(e) => setReviewer(e.target.value)} /></label>
                <label className="text-sm">Payout (optional)<input className={field} inputMode="decimal" placeholder={(c.approved_amount_cents / 100).toFixed(2)} value={amount} onChange={(e) => setAmount(e.target.value)} /></label>
                <label className="text-sm sm:col-span-2">Note<textarea className={field} rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></label>
              </div>
              {err && <p className="mt-2 text-sm text-danger">{err}</p>}
              <div className="mt-4 flex gap-2">
                <Button variant="primary" disabled={busy || !reviewer} onClick={() => review(true)}>Approve and pay out</Button>
                <Button variant="danger" disabled={busy || !reviewer} onClick={() => review(false)}>Reject claim</Button>
              </div>
            </Section>
          )}
        </>
      )}
    </Drawer>
  );
}
