export type TxState =
  | "CREATED" | "AWAITING_APPROVAL" | "AUTHORIZED" | "EXECUTING" | "COMPLETED"
  | "BLOCKED" | "FAILED" | "DISPUTED" | "RECOVERY_PENDING" | "REFUNDED";

export type Violation = { code: string; severity: "BLOCK" | "APPROVAL"; message: string; expected: string | null; actual: string | null };

export type Transaction = {
  id: string; idempotency_key: string; correlation_id: string; mandate_id: string | null; mandate_version: number | null;
  agent_id: string; customer_id: string | null; merchant_id: string; sku: string; product_name: string;
  product_category: string; attributes: Record<string, string>; quantity: number; unit_amount_cents: number;
  amount_cents: number; currency: string; state: TxState; decision: string; violations: Violation[];
  execution_slot: number | null; approved_by_customer: boolean; payment_ref: string | null;
  captured_amount_cents: number | null; fulfilled: Record<string, unknown> | null; event_cursors: Record<string, number>;
  created_at: string; updated_at: string;
};

export type TransactionDetail = Transaction & {
  history: { id: number; from_state: string | null; to_state: string; reason: string; at: string }[];
  provider_events: { id: number; event_id: string; source: string; type: string; sequence: number; status: string; detail: string | null; received_at: string }[];
  incident_id: string | null;
};

export type Overview = {
  transactions_monitored: number; by_state: Record<string, number>; blocked_attempts: number; awaiting_approval: number;
  incidents_detected: number; incidents_open: number; prevented_loss_cents: number; recovered_cents: number;
  simulated_payouts_cents: number; claims_in_review: number; volume_authorized_cents: number; currency: string;
};

export type Constraints = {
  max_amount_cents: number; currency: string; max_quantity: number; max_executions: number;
  allowed_merchants: string[] | null;
  product: { category: string; required_attributes: Record<string, string>; sku_allowlist: string[] | null };
  over_budget_action: "REQUIRE_APPROVAL" | "BLOCK"; hard_ceiling_pct: number;
};

export type Mandate = {
  id: string; customer_id: string; agent_id: string; current_version: number; status: "ACTIVE" | "REVOKED";
  executions_used: number; revoked_at: string | null; revoked_reason: string | null; created_at: string;
  customer_name: string | null; intent_text: string; constraints: Constraints; expires_at: string | null;
  content_hash: string; signature: string; signature_valid: boolean;
  versions?: { version: number; intent_text: string; constraints: Constraints; content_hash: string; created_at: string }[];
};

export type Finding = Violation;
export type Incident = {
  id: string; transaction_id: string; customer_id: string | null; type: string; severity: string; status: string;
  disputed_amount_cents: number; currency: string; summary: string | null; recommended_action: string | null;
  summary_source: string | null; detected_at: string; resolved_at: string | null;
  evidence: {
    authorized: { amount_cents: number; quantity: number; sku: string; attributes: Record<string, string>; mandate_id: string; mandate_version: number };
    executed: { captured_amount_cents: number; fulfilled: { sku?: string; quantity?: number; attributes?: Record<string, string>; product_category?: string }; payment_ref: string };
    findings: Finding[]; customer_message?: string;
  };
};

export type RecoveryAction = {
  id: string; incident_id: string; kind: string; status: string; amount_cents: number; attempts: number;
  max_attempts: number; next_attempt_at: string | null; merchant_ref: string | null; last_error: string | null;
  created_at: string; updated_at: string; transaction_id?: string; incident_type?: string; incident_status?: string; currency?: string;
};

export type ClaimCheck = { code: string; passed: boolean; hard: boolean; detail: string };
export type Claim = {
  id: string; incident_id: string; transaction_id: string; customer_id: string | null; policy_id: string; status: string;
  requested_amount_cents: number; approved_amount_cents: number; deductible_applied_cents: number; currency: string;
  checks: ClaimCheck[]; decision_reason: string | null; decided_by: string | null; reviewer_note: string | null;
  created_at: string; decided_at: string | null; paid_at: string | null;
};

export type IncidentDetail = Incident & { recovery_actions: RecoveryAction[]; claim: Claim | null };

export type AuditRecord = {
  seq: number; at: string; correlation_id: string; actor: string; action: string; entity_type: string;
  entity_id: string; data: Record<string, unknown>; prev_hash: string; hash: string;
};

export type ScenarioResult = {
  scenario: number; title: string; expected: string; correlation_id: string; customer_id: string; mandate_id: string;
  transaction_ids: string[]; final_states: Record<string, TxState>;
  steps: { action: string; entity_id: string; actor: string; at: string; data: Record<string, unknown> }[];
};

export type Merchant = { id: string; name: string; domain: string; supports_cancellation: boolean; refund_behavior: string; fulfillment_behavior: string };
export type Customer = { id: string; name: string; email: string };
export type Agent = { id: string; name: string; operator: string; status: string };
export type Policy = {
  id: string; code: string; version: number; name: string; deductible_cents: number; per_claim_limit_cents: number;
  aggregate_limit_cents: number; auto_approve_limit_cents: number; covered_incident_types: string[]; exclusions: string[];
};
export type LedgerEntry = { id: number; journal_id: string; account: string; direction: string; amount_cents: number; currency: string; kind: string; ref_type: string; ref_id: string; sandbox: boolean; created_at: string };
