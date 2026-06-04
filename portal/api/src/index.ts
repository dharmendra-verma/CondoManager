// SWA managed Functions entry point. The Functions host loads ONLY the file(s)
// named by package.json "main" (this file) and serves the functions registered
// here via the @azure/functions v4 programming model:
//   GET  /api/ticket?code=TKT-XXXXXXXX                     (CM-37, ticketHandler)
//   GET|POST            /api/admin/tenants                 (CM-56, tenantsCollectionHandler)
//   GET|PUT|DELETE      /api/admin/tenants/{id}            (CM-56, tenantItemHandler)
//
// CM-61: every app.http() registration MUST live in this entry file. On the SWA
// managed-functions host, registrations made in OTHER modules and pulled in via
// a side-effect `import "./tenants"` or a multi-file `main` glob did NOT get
// served at runtime (/api/admin/tenants returned a bare 404 even though the
// functions appeared in ARM) — the host's bundling drops them. Importing the
// handlers as USED values and calling app.http() here is what actually works.

import {
  type HttpRequest,
  type HttpResponseInit,
  type InvocationContext,
  app,
} from "@azure/functions";

import { lookupTicketByCode } from "./cosmos";
import { toPublicTicket } from "./shape";
import { tenantItemHandler, tenantsCollectionHandler } from "./tenants";

const CODE_RE = /^TKT-[0-9A-F]{8}$/;

export async function ticketHandler(
  req: HttpRequest,
  ctx: InvocationContext,
): Promise<HttpResponseInit> {
  const code = (req.query.get("code") ?? "").trim().toUpperCase();
  if (!CODE_RE.test(code)) {
    return { status: 400, jsonBody: { error: "invalid_code" } };
  }

  let doc: Record<string, unknown> | null;
  try {
    doc = await lookupTicketByCode(code);
  } catch (err) {
    ctx.error("ticket lookup failed", err);
    return { status: 502, jsonBody: { error: "lookup_failed" } };
  }

  const ticket = doc ? toPublicTicket(doc) : null;
  if (ticket === null) {
    return { status: 404, jsonBody: { error: "not_found" } };
  }
  return { status: 200, jsonBody: ticket };
}

app.http("ticket", {
  methods: ["GET"],
  authLevel: "anonymous",
  route: "ticket",
  handler: ticketHandler,
});

// CM-56 tenant admin CRUD — registered here (not in tenants.ts) so the SWA host
// actually serves them; see the CM-61 note at the top. Handlers + their logic
// live in ./tenants (unit-tested); this file only wires the routes.
app.http("tenantsCollection", {
  methods: ["GET", "POST"],
  authLevel: "anonymous", // TODO(auth): gated only by TENANT_ADMIN_ENABLED for now
  route: "admin/tenants",
  handler: tenantsCollectionHandler,
});

app.http("tenantItem", {
  methods: ["GET", "PUT", "DELETE"],
  authLevel: "anonymous", // TODO(auth): gated only by TENANT_ADMIN_ENABLED for now
  route: "admin/tenants/{id}",
  handler: tenantItemHandler,
});

// CM-61 DIAGNOSTIC (temporary — remove once root cause is fixed). Three probes
// that cross route-shape × config-rule to isolate why admin/tenants 404s:
//   /api/diagplain    single segment, NOT under a config rule
//   /api/diag/plain   multi  segment, NOT under a config rule
//   /api/admin/diag   multi  segment, UNDER the /api/admin/* config rule (like tenants)
const diagOk = (which: string) => async (): Promise<HttpResponseInit> => ({
  status: 200,
  jsonBody: { diag: which },
});
app.http("diagPlain", { methods: ["GET"], authLevel: "anonymous", route: "diagplain", handler: diagOk("plain") });
app.http("diagPlainNested", { methods: ["GET"], authLevel: "anonymous", route: "diag/plain", handler: diagOk("plain-nested") });
app.http("diagAdmin", { methods: ["GET"], authLevel: "anonymous", route: "admin/diag", handler: diagOk("admin-nested") });
