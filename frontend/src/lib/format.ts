export function money(cents: number | null | undefined, currency = "USD") {
  if (cents === null || cents === undefined) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(cents / 100);
}

export function ago(iso: string) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function time(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

export function humanize(code: string) {
  const s = code.replaceAll("_", " ").replaceAll(".", " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function short(id: string | null | undefined) {
  if (!id) return "—";
  const [p, rest] = id.split("_");
  return rest ? `${p}_${rest.slice(0, 6)}` : id.slice(0, 10);
}
