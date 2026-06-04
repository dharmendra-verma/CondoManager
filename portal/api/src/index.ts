// SWA managed Function: GET /api/ticket?code=TKT-XXXXXXXX (CM-37).
// Validates the code, looks the ticket up, and returns the tenant-safe public
// projection. Registered via the @azure/functions v4 programming model.
//
// CM-61 redeploy marker: forces a fresh function-app content hash so the SWA
// managed Functions backend provisions a new host that serves the tenant admin
// routes (the prior same-commit re-deploys were deduped and the live host kept
// serving the pre-fix routing). Safe no-op comment.

import {
  type HttpRequest,
  type HttpResponseInit,
  type InvocationContext,
  app,
} from "@azure/functions";

import { lookupTicketByCode } from "./cosmos";
import { toPublicTicket } from "./shape";
// CM-61: side-effect import so the tenant admin functions (app.http registrations
// in ./tenants) load at startup. The Functions host loads only this entry module
// (package.json "main"); without this import the /api/admin/tenants routes are
// never registered and the SWA returns a bare 404 — see CM-61.
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
