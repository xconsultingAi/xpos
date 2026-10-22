# Copyright (c) 2026, Ali Raza and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_workflow_name
from frappe.utils import flt, strip_html

COMPANY = "Dollar Junction"

# dollarj's "Sales Invoice Franchise Receipt" workflow: Draft -> Pending -> Received
STATE_PENDING = "Pending Franchise Receipt"
ACTION_SEND = "Send to Franchise"
ACTION_RECEIVE = "Receive"


def get_user_branches(user=None):
	"""The user's branches as {Customer: franchise_warehouse}, for every Customer they
	are the franchise_user of."""
	user = user or frappe.session.user
	rows = frappe.get_all(
		"Customer",
		filters={"franchise_user": user, "franchise_warehouse": ["is", "set"], "disabled": 0},
		fields=["name", "franchise_warehouse"],
		ignore_permissions=True,
	)
	return {r.name: r.franchise_warehouse for r in rows}


def get_branch_draft_invoices(branches, fields=None):
	"""The company's draft Sales Invoices to one of the branch Customers. Matched on the
	customer rather than the invoice's own franchise_warehouse, which stays empty on an
	invoice saved before its customer was made a franchise branch."""
	if not branches:
		return []

	return frappe.get_all(
		"Sales Invoice",
		filters={
			"docstatus": 0,
			"company": COMPANY,
			"customer": ["in", list(branches)],
			"is_pos": 0,
			"is_return": 0,
		},
		fields=fields or ["name"],
		order_by="posting_date desc, name desc",
		ignore_permissions=True,
	)


@frappe.whitelist()
def get_draft_invoices():
	branches = get_user_branches()
	warehouses = sorted(set(branches.values()))
	headers = get_branch_draft_invoices(
		branches,
		fields=[
			"name",
			"posting_date",
			"customer",
			"customer_name",
			"franchise_warehouse",
			"workflow_state",
			"grand_total",
		],
	)
	if not headers:
		return {"branches": warehouses, "invoices": []}

	invoices = {
		h.name: {
			"name": h.name,
			"posting_date": h.posting_date,
			"customer_name": h.customer_name,
			"branch": h.franchise_warehouse or branches.get(h.customer),
			"workflow_state": h.workflow_state,
			"grand_total": flt(h.grand_total),
			"items": [],
		}
		for h in headers
	}

	items = frappe.get_all(
		"Sales Invoice Item",
		filters={"parent": ["in", list(invoices)], "parenttype": "Sales Invoice"},
		fields=["parent", "item_code", "item_name", "uom", "qty", "rate", "amount"],
		order_by="idx asc",
		ignore_permissions=True,
	)
	for item in items:
		invoices[item.parent]["items"].append(
			{
				"item_code": item.item_code,
				"item_name": item.item_name,
				"uom": item.uom,
				"qty": flt(item.qty),
				"rate": flt(item.rate),
				"amount": flt(item.amount),
			}
		)

	return {"branches": warehouses, "invoices": list(invoices.values())}


@frappe.whitelist(methods=["POST"])
def approve_invoices(invoices):
	"""Receive the given draft invoices through the franchise workflow, which submits
	them and creates the franchise Purchase Invoice. Each one is checked again against
	the user's branches, so a stale page or a crafted request cannot approve another
	branch's invoice."""
	invoices = frappe.parse_json(invoices) or []
	allowed = {d.name for d in get_branch_draft_invoices(get_user_branches())}
	has_workflow = bool(get_workflow_name("Sales Invoice"))

	submitted, failed = [], []
	for name in invoices:
		if name not in allowed:
			failed.append({"name": name, "error": _("Not a draft invoice of your branch")})
			continue
		try:
			doc = frappe.get_doc("Sales Invoice", name)
			if not has_workflow:
				_refresh_franchise_fields(doc)
				doc.submit()
			else:
				if doc.get("workflow_state") != STATE_PENDING:
					_send_to_franchise(doc)
				# apply_workflow reloads the doc, so it sees the Pending state
				apply_workflow(doc, ACTION_RECEIVE)
			frappe.db.commit()
			submitted.append(name)
		except Exception as e:
			frappe.db.rollback()
			error = _error_text(e)
			frappe.clear_messages()
			failed.append({"name": name, "error": error})

	return {"submitted": submitted, "failed": failed}


def _refresh_franchise_fields(doc):
	"""An invoice saved before its customer became a franchise branch has no
	franchise_warehouse/franchise_user; a save re-fetches them for the workflow condition."""
	if not doc.get("franchise_warehouse") or not doc.get("franchise_user"):
		doc.flags.ignore_permissions = True
		doc.save()


def _send_to_franchise(doc):
	"""Move a Draft to Pending Franchise Receipt. dollarj hides Draft invoices from
	franchise owners, so this step runs as Administrator; the caller has already checked
	the invoice belongs to the user's branch. Receive then runs as the user."""
	user = frappe.session.user
	frappe.set_user("Administrator")
	try:
		_refresh_franchise_fields(doc)
		apply_workflow(doc, ACTION_SEND)
	finally:
		frappe.set_user(user)


def _error_text(e):
	"""str() of a bare frappe.PermissionError is empty; fall back to the last message."""
	text = strip_html(str(e)).strip()
	if not text:
		messages = [frappe.parse_json(m).get("message") for m in (frappe.message_log or [])]
		text = strip_html(next((m for m in reversed(messages) if m), "")).strip()
	return text or e.__class__.__name__
