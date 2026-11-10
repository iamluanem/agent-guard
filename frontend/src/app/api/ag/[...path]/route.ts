// Backend-for-frontend proxy: the browser never sees AG_ADMIN_API_KEY.
const API = process.env.AG_API_URL ?? "http://localhost:8000";
const ADMIN_KEY = process.env.AG_ADMIN_API_KEY;

async function forward(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const url = new URL(req.url);
  const target = `${API}/${path.map(encodeURIComponent).join("/")}${url.search}`;
  const headers = new Headers({ accept: req.headers.get("accept") ?? "application/json" });
  const ct = req.headers.get("content-type");
  if (ct) headers.set("content-type", ct);
  const cid = req.headers.get("x-correlation-id");
  if (cid) headers.set("x-correlation-id", cid);
  if (ADMIN_KEY) headers.set("x-admin-key", ADMIN_KEY);

  const upstream = await fetch(target, {
    method: req.method,
    headers,
    body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.text(),
    cache: "no-store",
    signal: req.signal,
  }).catch(() => null);

  if (!upstream) {
    return Response.json(
      { error: "api_unreachable", message: `AgentGuard API is not reachable at ${API}. Start it and reload.` },
      { status: 502 },
    );
  }
  const out = new Headers();
  for (const h of ["content-type", "x-correlation-id", "cache-control"]) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const GET = forward;
export const POST = forward;
