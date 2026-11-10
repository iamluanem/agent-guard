"use client";

import { Fragment, useState } from "react";
import { Button, Empty, Notice, PageHeader, Table, Td } from "@/components/ui";
import { api, useApi, useQueryParam } from "@/lib/api";
import { humanize, short, time } from "@/lib/format";
import type { AuditRecord } from "@/lib/types";

type Filters = { entity_id: string; correlation_id: string; action: string };
type Verify = { valid: boolean; records_checked: number; head_hash?: string; broken_at_seq?: number };

export default function AuditPage() {
  const fromUrl = {
    entity_id: useQueryParam("entity_id") ?? "",
    correlation_id: useQueryParam("correlation_id") ?? "",
    action: useQueryParam("action") ?? "",
  };
  const [edited, setQ] = useState<Filters | null>(null);
  const [chosen, setApplied] = useState<Filters | null>(null);
  const applied = chosen ?? fromUrl;
  const q = edited ?? applied;
  const [verify, setVerify] = useState<Verify | null>(null);
  const [vErr, setVErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);


  const qs = new URLSearchParams(Object.entries(applied).filter(([, v]) => v)).toString();
  const { data, error } = useApi<AuditRecord[]>(`/v1/audit?limit=300${qs ? `&${qs}` : ""}`);

  async function runVerify() {
    setVErr(null);
    try {
      setVerify(await api<Verify>("/v1/audit/verify"));
    } catch (e) {
      setVErr((e as Error).message);
    }
  }

  const field = "mt-1 w-full rounded-md border border-rule-strong bg-surface px-3 py-1.5 text-sm";
  return (
    <>
      <PageHeader
        title="Audit trail"
        lede="Every decision, state change and money movement, in order. Each record includes the hash of the one before it, so any later edit breaks the chain."
        actions={<Button variant="primary" onClick={runVerify}>Verify chain</Button>}
      />
      {vErr && <Notice>{vErr}</Notice>}
      {verify && (
        <div role="status" className={`mb-6 rounded-md px-4 py-3 text-sm ${verify.valid ? "bg-prevented-soft text-prevented" : "bg-danger-soft text-danger"}`}>
          {verify.valid ? (
            <>Chain intact: {verify.records_checked} records verified. Head <span className="id break-all">{verify.head_hash}</span></>
          ) : (
            <>Chain broken at record #{verify.broken_at_seq} after {verify.records_checked} valid records. That record was modified after it was written.</>
          )}
        </div>
      )}

      <form className="mb-5 grid gap-3 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); setApplied(q); }}>
        <label className="text-sm">Entity ID<input className={field} value={q.entity_id} onChange={(e) => setQ({ ...q, entity_id: e.target.value.trim() })} placeholder="txn_…, inc_…, clm_…" /></label>
        <label className="text-sm">Correlation ID<input className={field} value={q.correlation_id} onChange={(e) => setQ({ ...q, correlation_id: e.target.value.trim() })} placeholder="cor_…" /></label>
        <label className="text-sm">Action starts with<input className={field} value={q.action} onChange={(e) => setQ({ ...q, action: e.target.value.trim() })} placeholder="claim." /></label>
        <div className="flex gap-2">
          <Button type="submit" variant="secondary">Filter</Button>
          <Button type="button" variant="quiet" onClick={() => { const z = { entity_id: "", correlation_id: "", action: "" }; setQ(z); setApplied(z); }}>Clear</Button>
        </div>
      </form>

      {error && <Notice>{error}</Notice>}
      {data?.length === 0 && <Empty>No records match these filters.</Empty>}
      {data && data.length > 0 && (
        <Table head={["#", "When", "Action", "Actor", "Entity", "Correlation", "Hash"]}>
          {data.map((r) => (
            <Fragment key={r.seq}>
              <tr className="cursor-pointer hover:bg-paper" onClick={() => setExpanded(expanded === r.seq ? null : r.seq)}
                aria-expanded={expanded === r.seq}>
                <Td className="text-muted">{r.seq}</Td>
                <Td className="whitespace-nowrap text-muted">{time(r.at)}</Td>
                <Td className="whitespace-nowrap font-medium">{humanize(r.action)}</Td>
                <Td className="id text-muted">{r.actor}</Td>
                <Td><button className="id text-recovered underline" onClick={(e) => { e.stopPropagation(); const n = { ...q, entity_id: r.entity_id }; setQ(n); setApplied(n); }}>{short(r.entity_id)}</button></Td>
                <Td><button className="id text-recovered underline" onClick={(e) => { e.stopPropagation(); const n = { ...q, correlation_id: r.correlation_id }; setQ(n); setApplied(n); }}>{short(r.correlation_id)}</button></Td>
                <Td className="id text-faint">{r.hash.slice(0, 10)}</Td>
              </tr>
              {expanded === r.seq && (
                <tr className="bg-paper">
                  <td colSpan={7} className="px-4 py-3">
                    <pre className="id max-h-80 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(r.data, null, 2)}</pre>
                    <p className="id mt-2 break-all text-xs text-muted">prev {r.prev_hash}<br />hash {r.hash}</p>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </Table>
      )}
    </>
  );
}
