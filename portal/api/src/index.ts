// SWA managed Function: GET /api/ticket?code=TKT-XXXXXXXX (CM-37).
// Validates the code, looks the ticket up, and returns the tenant-safe public
// projection. Registered via the @azure/functions v4 programming model.

import {
  type HttpRequest,
  type HttpResponseInit,
  type InvocationContext,
  app,
} from "@azure/functions";

import { lookupTicketByCode } from "./cosmos";
import { toPublicTicket } from "./shape";
// CM-61: belt-and-suspenders side-effect import of the tenant admin functions.
// The PRIMARY discovery mechanism is the package.json "main" glob
// ("dist/src/{index.js,tenants.js}"), which makes the Functions host load
// tenants.js directly as an entry module. On the SWA managed-functions host a
// single-file "main" + this side-effect import alone did NOT register the
// tenant routes at runtime (/api/admin/tenants returned a bare 404 even though
// build-time analysis listed them) — the glob is what fixes it. See CM-61.
import "./tenants";

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
