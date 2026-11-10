"use client";

import Link from "next/link";
import { useState } from "react";
import { Button, Empty, Notice, PageHeader, StateChip, Table, Td } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { ago, humanize, money, short } from "@/lib/format";
import type { RecoveryAction } from "@/lib/types";

const STAGES = [
  { status: "REQUESTED", label: "Waiting on merchant" },
  { status: "RETRY_SCHEDULED", label: "Retry scheduled" },
  { status: "SUCCEEDED", label: "Recovered" },
  { status: "FAILED", label: "Failed, escalated" },
];

export default function RecoveryPage() {
  const { data, error, reload } = useApi<RecoveryAction[]>("/v1/recovery");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function runDue() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ delivered_events: number }>("/v1/recovery/run-due", { method: "POST" });
      setMsg(r.delivered_events ? `Retried due actions; ${r.delivered_events} merchant responses received.` : "No retries are due yet. Backoff timers are still running.");
      await reload();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const totals = STAGES.map((s) => {
    const rows = data?.filter((a) => a.status === s.status) ?? [];
    return { ...s, count: rows.length, cents: rows.reduce((t, a) => t + a.amount_cents, 0) };
  });

  return (
    <>
      <PageHeader
        title="Recovery center"
        lede="Cancellations and refunds requested from merchants. A request is not money back: funds count as recovered only when the merchant confirms the refund."
        actions={<Button onClick={runDue} disabled={busy}>{busy ? "Retrying…" : "Retry due actions now"}</Button>}
      />
      {msg && <div className="mb-4"><Notice tone="neutral">{msg}</Notice></div>}
      {error && <Notice>{error}</Notice>}

      <dl className="mb-8 grid grid-cols-2 gap-6 sm:grid-cols-4">
        {totals.map((t) => (
          <div key={t.status}>
            <dt className="text-sm text-muted">{t.label}</dt>
            <dd className="mt-1 text-2xl font-semibold tracking-tight">{money(t.cents)}</dd>
            <dd className="text-xs text-muted">{t.count} action{t.count === 1 ? "" : "s"}</dd>
          </div>
        ))}
      </dl>

      {data?.length === 0 && <Empty>Nothing to recover. Incidents start recovery automatically.</Empty>}
      {data && data.length > 0 && (
        <Table head={["Action", "Incident", "Amount", "Attempts", "Merchant response", "Status", "Updated"]}>
          {data.map((a) => (
            <tr key={a.id}>
              <Td>{humanize(a.kind)}<div className="id text-muted">{short(a.id)}</div></Td>
              <Td><Link href={`/incidents?open=${a.incident_id}`} className="id text-recovered underline">{short(a.incident_id)}</Link>
                <div className="text-xs text-muted">{humanize(a.incident_type ?? "")}</div></Td>
              <Td className="whitespace-nowrap">{money(a.amount_cents, a.currency)}</Td>
              <Td>{a.attempts}/{a.max_attempts}
                {a.next_attempt_at && a.status === "RETRY_SCHEDULED" && <div className="text-xs text-muted">next {new Date(a.next_attempt_at).toLocaleTimeString()}</div>}</Td>
              <Td className="max-w-[32ch] text-muted">{a.last_error ?? (a.merchant_ref ? <span className="id">{a.merchant_ref}</span> : "—")}</Td>
              <Td><StateChip state={a.status} /></Td>
              <Td className="whitespace-nowrap text-muted">{ago(a.updated_at)}</Td>
            </tr>
          ))}
        </Table>
      )}
    </>
  );
}
