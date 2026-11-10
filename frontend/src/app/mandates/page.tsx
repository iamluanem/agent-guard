"use client";

import { Fragment, useState } from "react";
import { Button, Chip, Drawer, Empty, Notice, PageHeader, Section, StateChip, Table, Td } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { humanize, money, short, time } from "@/lib/format";
import type { Agent, Constraints, Customer, Mandate, Merchant } from "@/lib/types";

export default function MandatesPage() {
  const { data, error } = useApi<Mandate[]>("/v1/mandates?limit=200");
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Protection policies"
        lede="Mandates are what customers allow their agents to do. Each version is signed; changes create a new version and revocation takes effect on the next attempt."
        actions={<Button variant="primary" onClick={() => setCreating(true)}>New mandate</Button>}
      />
      {error && <Notice>{error}</Notice>}
      {data?.length === 0 && <Empty>No mandates yet. Create one from a customer&apos;s instruction.</Empty>}
      {data && data.length > 0 && (
        <Table head={["Customer", "Instruction", "Budget", "Limits", "Used", "Signature", "Status"]}>
          {data.map((m) => (
            <tr key={m.id} className="cursor-pointer hover:bg-paper" onClick={() => setOpen(m.id)}>
              <Td>
                <div>{m.customer_name}</div>
                <div className="id text-muted">{short(m.id)} v{m.current_version}</div>
              </Td>
              <Td className="max-w-[34ch] text-muted">{m.intent_text}</Td>
              <Td className="whitespace-nowrap">{money(m.constraints.max_amount_cents, m.constraints.currency)}</Td>
              <Td className="text-xs text-muted">
                {m.constraints.product.category}
                {Object.entries(m.constraints.product.required_attributes).map(([k, v]) => `, ${k} ${v}`)}
                <br />max {m.constraints.max_quantity} · {m.constraints.allowed_merchants ? `${m.constraints.allowed_merchants.length} merchants` : "any merchant"}
              </Td>
              <Td>{m.executions_used}/{m.constraints.max_executions}</Td>
              <Td>{m.signature_valid ? <Chip tone="ok">Valid</Chip> : <Chip tone="danger">Invalid</Chip>}</Td>
              <Td><StateChip state={m.status} /></Td>
            </tr>
          ))}
        </Table>
      )}
      <MandateDrawer id={open} onClose={() => setOpen(null)} />
      <CreateMandate open={creating} onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setOpen(id); }} />
    </>
  );
}

function MandateDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data: m, reload } = useApi<Mandate>(id ? `/v1/mandates/${id}` : null);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const show = m && m.id === id;

  async function revoke() {
    setErr(null);
    try {
      await api(`/v1/mandates/${id}/revoke`, { method: "POST", body: { reason: reason || "revoked by customer" } });
      await reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <Drawer open={!!id} onClose={onClose}
      title={<><div className="id text-muted">{id}</div><div className="mt-0.5 text-lg font-semibold">{show ? m.customer_name : "Loading…"}</div></>}>
      {show && (
        <>
          <blockquote className="border-l-2 border-ink pl-4 text-[1.0625rem] leading-relaxed">{m.intent_text}</blockquote>
          <Section title="Constraints">
            <ConstraintList c={m.constraints} expires={m.expires_at} />
          </Section>
          <Section title="Signature" aside={m.signature_valid ? <Chip tone="ok">Verified</Chip> : <Chip tone="danger">Failed</Chip>}>
            <dl className="grid grid-cols-[110px_1fr] gap-x-4 gap-y-1.5 text-sm">
              <dt className="text-muted">Content hash</dt><dd className="id break-all">{m.content_hash}</dd>
              <dt className="text-muted">HMAC-SHA256</dt><dd className="id break-all">{m.signature}</dd>
            </dl>
          </Section>
          <Section title={`Versions (${m.versions?.length ?? 1})`}>
            <ol className="divide-y divide-rule border-y border-rule text-sm">
              {m.versions?.map((v) => (
                <li key={v.version} className="flex justify-between gap-3 py-2">
                  <span>Version {v.version} · {money(v.constraints.max_amount_cents, v.constraints.currency)} budget</span>
                  <span className="text-muted">{time(v.created_at)}</span>
                </li>
              ))}
            </ol>
          </Section>
          <Section title="Revocation">
            {m.status === "REVOKED" ? (
              <p className="text-sm text-muted">Revoked {time(m.revoked_at)}: {m.revoked_reason}</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                <label className="sr-only" htmlFor="reason">Reason</label>
                <input id="reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (optional)"
                  className="min-w-0 flex-1 rounded-md border border-rule-strong px-3 py-1.5 text-sm" />
                <Button variant="danger" onClick={revoke}>Revoke mandate</Button>
              </div>
            )}
            {err && <p className="mt-2 text-sm text-danger">{err}</p>}
          </Section>
        </>
      )}
    </Drawer>
  );
}

function ConstraintList({ c, expires }: { c: Constraints; expires: string | null }) {
  const rows: [string, string][] = [
    ["Budget", `${money(c.max_amount_cents, c.currency)} per purchase`],
    ["Over budget", c.over_budget_action === "BLOCK" ? "Block" : `Ask customer up to +${c.hard_ceiling_pct}%, block beyond`],
    ["Product", c.product.category],
    ["Must match", Object.entries(c.product.required_attributes).map(([k, v]) => `${k} ${v}`).join(", ") || "—"],
    ["SKUs", c.product.sku_allowlist?.join(", ") ?? "any"],
    ["Quantity", `up to ${c.max_quantity}`],
    ["Executions", `${c.max_executions} purchase${c.max_executions > 1 ? "s" : ""}`],
    ["Merchants", c.allowed_merchants?.join(", ") ?? "any"],
    ["Expires", time(expires)],
  ];
  return (
    <dl className="grid grid-cols-[110px_1fr] gap-x-4 gap-y-1.5 text-sm">
      {rows.map(([k, v]) => (<Fragment key={k}><dt className="text-muted">{k}</dt><dd>{v}</dd></Fragment>))}
    </dl>
  );
}

type Proposal = { product_category: string; quantity: number; attributes: Record<string, string>; max_amount_cents: number | null; currency: string; confidence: number; ambiguities: string[] };

function CreateMandate({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const customers = useApi<Customer[]>(open ? "/v1/customers" : null, { live: false });
  const agents = useApi<Agent[]>(open ? "/v1/agents" : null, { live: false });
  const merchants = useApi<Merchant[]>(open ? "/v1/merchants" : null, { live: false });
  const [text, setText] = useState("Buy one pair of running shoes, size 38, for no more than $150.");
  const [proposal, setProposal] = useState<{ p: Proposal; provider: string } | null>(null);
  const [form, setForm] = useState({ customer_id: "", agent_id: "", category: "", attrs: "", budget: "", currency: "USD", qty: "1",
    merchants: [] as string[], over: "REQUIRE_APPROVAL", days: "7" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form, v: string | string[]) => setForm((f) => ({ ...f, [k]: v }));

  async function read() {
    setErr(null);
    try {
      const r = await api<{ proposal: Proposal; provider: string }>("/v1/intents/extract", { method: "POST", body: { text } });
      setProposal({ p: r.proposal, provider: r.provider });
      setForm((f) => ({ ...f, category: r.proposal.product_category, qty: String(r.proposal.quantity),
        attrs: Object.entries(r.proposal.attributes).map(([k, v]) => `${k}=${v}`).join(", "),
        budget: r.proposal.max_amount_cents ? String(r.proposal.max_amount_cents / 100) : "", currency: r.proposal.currency }));
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function create() {
    setErr(null);
    setBusy(true);
    try {
      const attrs = Object.fromEntries(form.attrs.split(",").map((s) => s.trim()).filter(Boolean).map((s) => s.split("=").map((x) => x.trim())));
      const m = await api<Mandate>("/v1/mandates", { method: "POST", body: {
        customer_id: form.customer_id || customers.data?.[0]?.id, agent_id: form.agent_id || agents.data?.[0]?.id, intent_text: text,
        expires_at: new Date(Date.now() + Number(form.days) * 86400_000).toISOString(),
        constraints: { max_amount_cents: Math.round(Number(form.budget) * 100), currency: form.currency, max_quantity: Number(form.qty),
          max_executions: 1, allowed_merchants: form.merchants.length ? form.merchants : null,
          product: { category: form.category, required_attributes: attrs }, over_budget_action: form.over, hard_ceiling_pct: 50 } } });
      setProposal(null);
      onCreated(m.id);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-md border border-rule-strong bg-surface px-3 py-1.5 text-sm";
  return (
    <Drawer open={open} onClose={onClose} title={<div className="text-lg font-semibold">New mandate</div>}>
      <label className="text-sm font-medium" htmlFor="intent">Customer&apos;s instruction</label>
      <textarea id="intent" rows={3} value={text} onChange={(e) => setText(e.target.value)} className={field} />
      <div className="mt-2"><Button onClick={read}>Read constraints from instruction</Button></div>

      {proposal && (
        <div className="mt-4 rounded-md bg-paper px-4 py-3 text-sm">
          <p>
            Proposed by the <span className="font-medium">{proposal.provider}</span> interpreter with {Math.round(proposal.p.confidence * 100)}% confidence.
            It is a suggestion: check the fields below before signing.
          </p>
          {proposal.p.ambiguities.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-pending">{proposal.p.ambiguities.map((a) => <li key={a}>{humanize(a)}</li>)}</ul>
          )}
        </div>
      )}

      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="text-sm">Customer
          <select className={field} value={form.customer_id} onChange={(e) => set("customer_id", e.target.value)}>
            {customers.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label className="text-sm">Agent
          <select className={field} value={form.agent_id} onChange={(e) => set("agent_id", e.target.value)}>
            {agents.data?.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
        <label className="text-sm">Product category<input className={field} value={form.category} onChange={(e) => set("category", e.target.value)} /></label>
        <label className="text-sm">Must match (key=value, …)<input className={field} value={form.attrs} onChange={(e) => set("attrs", e.target.value)} /></label>
        <label className="text-sm">Budget per purchase<input className={field} inputMode="decimal" value={form.budget} onChange={(e) => set("budget", e.target.value)} /></label>
        <label className="text-sm">Currency<input className={field} maxLength={3} value={form.currency} onChange={(e) => set("currency", e.target.value.toUpperCase())} /></label>
        <label className="text-sm">Maximum quantity<input className={field} inputMode="numeric" value={form.qty} onChange={(e) => set("qty", e.target.value)} /></label>
        <label className="text-sm">Valid for (days)<input className={field} inputMode="numeric" value={form.days} onChange={(e) => set("days", e.target.value)} /></label>
        <label className="text-sm sm:col-span-2">When the price is over budget
          <select className={field} value={form.over} onChange={(e) => set("over", e.target.value)}>
            <option value="REQUIRE_APPROVAL">Ask the customer (up to +50%), block beyond</option>
            <option value="BLOCK">Always block</option>
          </select>
        </label>
        <fieldset className="text-sm sm:col-span-2">
          <legend>Allowed merchants <span className="text-muted">(none selected = any)</span></legend>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
            {merchants.data?.map((m) => (
              <label key={m.id} className="flex items-center gap-1.5">
                <input type="checkbox" checked={form.merchants.includes(m.id)}
                  onChange={(e) => set("merchants", e.target.checked ? [...form.merchants, m.id] : form.merchants.filter((x) => x !== m.id))} />
                {m.name}
              </label>
            ))}
          </div>
        </fieldset>
      </div>
      {err && <p className="mt-4 text-sm text-danger">{err}</p>}
      <div className="mt-6 flex gap-2">
        <Button variant="primary" disabled={busy || !form.category || !form.budget} onClick={create}>Sign and create mandate</Button>
        <Button onClick={onClose}>Cancel</Button>
      </div>
    </Drawer>
  );
}
