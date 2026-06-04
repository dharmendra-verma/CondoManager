// Tenant admin page glue (CM-56). DOM wiring only — all validation/format logic
// lives in ./admin (unit-tested). Talks to the gated /api/admin/tenants CRUD
// API. Rows are built with textContent (not innerHTML) since tenant data is
// operator-entered free text.
//
// TODO(auth): this page is unauthenticated; the API it calls is disabled unless
// TENANT_ADMIN_ENABLED is set, so in staging/prod the list simply fails to load.

import {
  type RawForm,
  type Tenant,
  formatOptional,
  sortTenants,
  summarizeErrors,
  validateTenantForm,
} from "./admin";

// /api/tenants (NOT /api/admin/*): the SWA edge does not forward /api/admin/*
// paths to the managed Functions backend — they bare-404 before reaching the
// function (confirmed for CM-61). Non-admin /api paths forward fine.
const API = "/api/tenants";

const form = document.querySelector<HTMLFormElement>("#tenant-form");
const errorEl = document.querySelector<HTMLParagraphElement>("#form-error");
const tableBody = document.querySelector<HTMLTableSectionElement>("#tenants-body");
const formTitle = document.querySelector<HTMLElement>("#form-title");
const submitBtn = document.querySelector<HTMLButtonElement>("#submit-btn");
const cancelBtn = document.querySelector<HTMLButtonElement>("#cancel-btn");
const emptyEl = document.querySelector<HTMLElement>("#empty");

let editingId: string | null = null;

function input(name: string): HTMLInputElement | HTMLTextAreaElement | null {
  return form?.querySelector(`[name="${name}"]`) ?? null;
}

function readForm(): RawForm {
  const get = (name: string): string => input(name)?.value ?? "";
  return {
    name: get("name"),
    unit: get("unit"),
    mobile: get("mobile"),
    email: get("email"),
    notes: get("notes"),
    spouse: get("spouse"),
  };
}

function fillForm(tenant: Tenant | null): void {
  const set = (name: string, value: string): void => {
    const el = input(name);
    if (el) {
      el.value = value;
    }
  };
  set("name", tenant?.name ?? "");
  set("unit", tenant?.unit ?? "");
  set("mobile", tenant?.mobile ?? "");
  set("email", tenant?.email ?? "");
  set("notes", tenant?.notes ?? "");
  set("spouse", tenant?.spouse ?? "");
}

function showError(message: string): void {
  if (!errorEl) {
    return;
  }
  errorEl.textContent = message;
  errorEl.hidden = message === "";
}

function enterEditMode(tenant: Tenant): void {
  editingId = tenant.id;
  fillForm(tenant);
  if (formTitle) {
    formTitle.textContent = `Edit ${tenant.name}`;
  }
  if (submitBtn) {
    submitBtn.textContent = "Save changes";
  }
  if (cancelBtn) {
    cancelBtn.hidden = false;
  }
  showError("");
  form?.scrollIntoView({ behavior: "smooth" });
}

function exitEditMode(): void {
  editingId = null;
  fillForm(null);
  if (formTitle) {
    formTitle.textContent = "Add tenant";
  }
  if (submitBtn) {
    submitBtn.textContent = "Add tenant";
  }
  if (cancelBtn) {
    cancelBtn.hidden = true;
  }
  showError("");
}

function cell(row: HTMLTableRowElement, text: string): void {
  const td = document.createElement("td");
  td.textContent = text;
  row.appendChild(td);
}

function renderTenants(tenants: Tenant[]): void {
  if (!tableBody) {
    return;
  }
  tableBody.replaceChildren();
  const sorted = sortTenants(tenants);
  if (emptyEl) {
    emptyEl.hidden = sorted.length > 0;
  }
  for (const tenant of sorted) {
    const row = document.createElement("tr");
    cell(row, tenant.unit);
    cell(row, tenant.name);
    cell(row, tenant.mobile);
    cell(row, formatOptional(tenant.email));
    cell(row, formatOptional(tenant.spouse));
    cell(row, formatOptional(tenant.notes));

    const actions = document.createElement("td");
    actions.className = "actions";

    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "link";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => enterEditMode(tenant));

    const del = document.createElement("button");
    del.type = "button";
    del.className = "link danger";
    del.textContent = "Delete";
    del.addEventListener("click", () => void removeTenant(tenant));

    actions.append(edit, del);
    row.appendChild(actions);
    tableBody.appendChild(row);
  }
}

async function loadTenants(): Promise<void> {
  try {
    const res = await fetch(API);
    if (!res.ok) {
      showError(
        "Couldn't load tenants. Is the admin API enabled (TENANT_ADMIN_ENABLED)?",
      );
      return;
    }
    renderTenants((await res.json()) as Tenant[]);
  } catch {
    showError("Couldn't reach the tenant API.");
  }
}

async function submitForm(): Promise<void> {
  const validation = validateTenantForm(readForm());
  if (!validation.ok || validation.value === undefined) {
    showError(summarizeErrors(validation.errors));
    return;
  }
  const editing = editingId;
  const url = editing === null ? API : `${API}/${encodeURIComponent(editing)}`;
  const method = editing === null ? "POST" : "PUT";

  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: { "content-type": "application/json" },
      body: JSON.stringify(validation.value),
    });
  } catch {
    showError("Couldn't reach the tenant API.");
    return;
  }
  if (res.status === 409) {
    showError("That mobile number already belongs to another tenant.");
    return;
  }
  if (!res.ok) {
    showError("Save failed. Double-check the fields and try again.");
    return;
  }
  exitEditMode();
  await loadTenants();
}

async function removeTenant(tenant: Tenant): Promise<void> {
  if (!confirm(`Delete ${tenant.name} (unit ${tenant.unit})?`)) {
    return;
  }
  try {
    const res = await fetch(`${API}/${encodeURIComponent(tenant.id)}`, {
      method: "DELETE",
    });
    if (!res.ok && res.status !== 404) {
      showError("Delete failed. Please try again.");
      return;
    }
  } catch {
    showError("Couldn't reach the tenant API.");
    return;
  }
  if (editingId === tenant.id) {
    exitEditMode();
  }
  await loadTenants();
}

form?.addEventListener("submit", (event) => {
  event.preventDefault();
  void submitForm();
});
cancelBtn?.addEventListener("click", () => exitEditMode());

exitEditMode();
void loadTenants();
