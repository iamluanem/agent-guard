"use client";

import Link from "next/link";
import { useState } from "react";
import { TransactionDrawer } from "@/components/transaction-drawer";
import { Chip, Drawer, Empty, Notice, PageHeader, Section, StateChip, Table, Td } from "@/components/ui";
import { useApi, useQueryParam } from "@/lib/api";
import { ago, humanize, money, short, time } from "@/lib/format";
import type { Incident, IncidentDetail } from "@/lib/types";

export default function IncidentsPage() {
  const { data, error } = useApi<Incident[]>("/v1/incidents");
  const initial = useQueryParam("open");
  const [picked, setOpen] = useState<string | null | undefined>(undefined);
  const open = picked === undefined ? initial : picked;

  return (
    <>
      <PageHeader
        title="Incidents"
        lede="Purchases that went through but don't match what the customer authorized. Detection compares the captured payment and the fulfilled order against the mandate."
      />
      {error && <Notice>{error}</Notice>}
      {data?.length === 0 && <Empty>No incidents. Run scenario 5 or 6 from the overview to create one.</Empty>}
      {data && data.length > 0 && (
        <Table head={["Incident", "Type", "Disputed", "Summary", "Status", "Detected"]}>
          {data.map((i) => (
            <tr key={i.id} className="cursor-pointer hover:bg-paper" onClick={() => setOpen(i.id)}>
              <Td><button className="id text-recovered underline">{short(i.id)}</button></Td>
              <Td className="whitespace-nowrap">{humanize(i.type)}</Td>
              <Td className="whitespace-nowrap">{money(i.disputed_amount_cents, i.currency)}</Td>
              <Td className="max-w-[48ch] text-muted">{i.summary}</Td>
              <Td><StateChip state={i.status} /></Td>
              <Td className="whitespace-nowrap text-muted">{ago(i.detected_at)}</Td>
            </tr>
          ))}
        </Table>
      )}
      <IncidentDrawer id={open} onClose={() => setOpen(null)} />
    </>
  );
}

function IncidentDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data: i } = useApi<IncidentDetail>(id ? `/v1/incidents/${id}` : null);
  const [tx, setTx] = useState<string | null>(null);
  const show = i && i.id === id;
  const a = i?.evidence.authorized;
  const x = i?.evidence.executed;

  return (
    <>
      <Drawer open={!!id} onClose={onClose}
        title={<><div className="id text-muted">{id}</div>
          <div className="mt-0.5 text-lg font-semibold">{show ? `${humanize(i.type)} · ${money(i.disputed_amount_cents, i.currency)} disputed` : "Loading…"}</div></>}>
        {show && a && x && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <StateChip state={i.status} />
              <span className="text-sm text-muted">Detected {time(i.detected_at)}</span>
            </div>

            <Section title="Authorized vs. what happened">
              <table className="w-full border-y border-rule text-sm">
                <thead><tr className="text-left text-xs text-muted"><th className="py-2 font-medium" /><th className="py-2 font-medium">Authorized</th><th className="py-2 font-medium">Executed</th></tr></thead>
                <tbody className="divide-y divide-rule">
                  <Row label="Amount" a={money(a.amount_cents, i.currency)} b={money(x.captured_amount_cents, i.currency)} />
                  <Row label="Quantity" a={String(a.quantity)} b={String(x.fulfilled.quantity ?? "—")} />
                  <Row label="Attributes" a={fmt(a.attributes)} b={fmt(x.fulfilled.attributes ?? {})} />
                  <Row label="SKU" a={a.sku} b={x.fulfilled.sku ?? "—"} />
                </tbody>
              </table>
            </Section>

            <Section title="Explanation" aside={<Chip>{i.summary_source === "rules" ? "Rules-based narrative" : `Written by ${i.summary_source}`}</Chip>}>
              <p className="text-sm leading-relaxed">{i.summary}</p>
              <p className="mt-2 text-sm text-muted">Recommended: {i.recommended_action}</p>
              <p className="mt-2 text-xs text-muted">The narrative is advisory. Detection and amounts come from deterministic rules.</p>
            </Section>

            <Section title="Findings">
              <ul className="divide-y divide-rule border-y border-rule text-sm">
                {i.evidence.findings.map((f, k) => (
                  <li key={k} className="py-2"><span className="font-medium">{humanize(f.code)}</span>
                    <span className="text-muted"> · expected {f.code === "OVERCHARGE" ? money(Number(f.expected)) : f.expected ?? "—"}, got {f.code === "OVERCHARGE" ? money(Number(f.actual)) : f.actual ?? "—"}</span></li>
                ))}
              </ul>
            </Section>

            <Section title="Recovery">
              {i.recovery_actions.map((r) => (
                <div key={r.id} className="flex items-center justify-between border-b border-rule py-2 text-sm">
                  <span>{humanize(r.kind)} · attempt {r.attempts}/{r.max_attempts}{r.last_error ? <span className="text-muted"> · {r.last_error}</span> : null}</span>
                  <StateChip state={r.status} />
                </div>
              ))}
              <Link href="/recovery" className="mt-2 inline-block text-sm text-recovered underline">Open recovery center</Link>
            </Section>

            {i.claim && (
              <Section title="Protection claim">
                <div className="flex items-center justify-between text-sm">
                  <Link href={`/claims?open=${i.claim.id}`} className="id text-recovered underline">{i.claim.id}</Link>
                  <span className="flex items-center gap-2">{money(i.claim.approved_amount_cents, i.currency)} <StateChip state={i.claim.status} /></span>
                </div>
              </Section>
            )}

            <Section title="Transaction">
              <button className="id text-recovered underline" onClick={() => setTx(i.transaction_id)}>{i.transaction_id}</button>
            </Section>
          </>
        )}
      </Drawer>
      <TransactionDrawer id={tx} onClose={() => setTx(null)} />
    </>
  );
}

function fmt(o: Record<string, string>) {
  return Object.entries(o).map(([k, v]) => `${k} ${v}`).join(", ") || "—";
}

function Row({ label, a, b }: { label: string; a: string; b: string }) {
  const differs = a !== b;
  return (
    <tr>
      <th scope="row" className="py-2 pr-3 text-left font-normal text-muted">{label}</th>
      <td className="py-2">{a}</td>
      <td className={`py-2 ${differs ? "font-semibold text-danger" : ""}`}>{b}{differs && <span className="sr-only"> (differs)</span>}</td>
    </tr>
  );
}
