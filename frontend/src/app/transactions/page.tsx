"use client";

import { useState } from "react";
import { TransactionDrawer } from "@/components/transaction-drawer";
import { Empty, Notice, PageHeader, StateChip, Table, Td } from "@/components/ui";
import { useApi } from "@/lib/api";
import { ago, humanize, money, short } from "@/lib/format";
import type { Transaction, TxState } from "@/lib/types";

const FILTERS: (TxState | "ALL")[] = ["ALL", "AWAITING_APPROVAL", "COMPLETED", "BLOCKED", "RECOVERY_PENDING", "REFUNDED"];

export default function TransactionsPage() {
  const [filter, setFilter] = useState<TxState | "ALL">("ALL");
  const [open, setOpen] = useState<string | null>(null);
  const { data, error, loading } = useApi<Transaction[]>(`/v1/transactions?limit=300${filter === "ALL" ? "" : `&state=${filter}`}`);

  return (
    <>
      <PageHeader
        title="Live transactions"
        lede="Every purchase attempt from every agent, with the policy decision and payment state. The list updates as provider webhooks arrive."
      />
      <div role="tablist" aria-label="Filter by state" className="mb-4 flex flex-wrap gap-1">
        {FILTERS.map((f) => (
          <button key={f} role="tab" aria-selected={filter === f} onClick={() => setFilter(f)}
            className={`rounded-md px-2.5 py-1 text-sm ${filter === f ? "bg-ink text-white" : "text-muted hover:bg-surface"}`}>
            {f === "ALL" ? "All" : humanize(f)}
          </button>
        ))}
      </div>
      {error && <Notice>{error}</Notice>}
      {data && data.length === 0 && <Empty>No transactions in this state. Run a demo scenario from the overview.</Empty>}
      {data && data.length > 0 && (
        <Table head={["Transaction", "Product", "Merchant", "Amount", "Decision", "State", "When"]}>
          {data.map((t) => (
            <tr key={t.id} className="cursor-pointer hover:bg-paper" onClick={() => setOpen(t.id)}>
              <Td><button className="id text-recovered underline" onClick={() => setOpen(t.id)}>{short(t.id)}</button></Td>
              <Td>
                <div>{t.product_name}</div>
                <div className="text-xs text-muted">{t.quantity} × {Object.values(t.attributes).join(", ") || t.product_category}</div>
              </Td>
              <Td className="id">{t.merchant_id.replace("mer_", "")}</Td>
              <Td className="whitespace-nowrap text-right">{money(t.amount_cents, t.currency)}</Td>
              <Td>
                {t.violations.length ? (
                  <span className="text-sm">{t.violations.map((v) => humanize(v.code)).join(", ")}</span>
                ) : <span className="text-sm text-muted">Passed</span>}
              </Td>
              <Td><StateChip state={t.state} /></Td>
              <Td className="whitespace-nowrap text-muted">{ago(t.created_at)}</Td>
            </tr>
          ))}
        </Table>
      )}
      {loading && !data && <p className="text-sm text-muted">Loading transactions…</p>}
      <TransactionDrawer id={open} onClose={() => setOpen(null)} />
    </>
  );
}
