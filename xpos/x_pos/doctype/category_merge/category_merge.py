# Copyright (c) 2026, Ali Raza and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_link_to_form


DEFAULT_COMPANY = "Dollar Junction"


def get_user_branch(user=None):
	"""The user's branch warehouse: their POS Profile's warehouse, else the
	franchise_warehouse of a Customer they are the franchise_user of."""
	user = user or frappe.session.user
	profile = frappe.db.get_value("POS Profile User", {"user": user, "parenttype": "POS Profile"}, "parent")
	if profile:
		warehouse = frappe.db.get_value("POS Profile", profile, "warehouse")
		if warehouse:
			return warehouse
	meta = frappe.get_meta("Customer")
	if meta.has_field("franchise_user") and meta.has_field("franchise_warehouse"):
		return frappe.db.get_value(
			"Customer", {"franchise_user": user, "franchise_warehouse": ["is", "set"]}, "franchise_warehouse"
		)
	return None


class CategoryMerge(Document):
	"""Merge the stock of items, usually one Item Category, into target items with Repack
	Stock Entries. Each row has its own warehouse and target item; the target item is put
	back into the row's warehouse."""

	def validate(self):
		# Company and Warehouse are read-only: Dollar Junction and the user's branch. They
		# are set here so a request cannot point the tool at another branch. A branch may
		# sit in the franchise company, so each Repack is posted in the warehouse's own
		# company.
		self.set_fixed_header()
		self.set_totals()

	def set_fixed_header(self):
		if frappe.db.exists("Company", DEFAULT_COMPANY):
			self.company = DEFAULT_COMPANY
		branch = get_user_branch()
		if branch:
			self.warehouse = branch

	def set_totals(self):
		for row in self.items:
			row.amount = flt(row.qty) * flt(row.valuation_rate)
		self.total_qty = sum(flt(row.qty) for row in self.items)
		self.total_amount = sum(flt(row.amount) for row in self.items)

	@frappe.whitelist()
	def get_items(self):
		"""Load every in-stock item of the category held in the warehouse, at its full
		balance. Batch and serial items are left out: a Repack row for them needs the
		exact batch or serial numbers picked by hand."""
		self.set_fixed_header()
		if not self.warehouse:
			frappe.throw(_("Set the Warehouse first."))
		# these rows carry stock qty, not an invoice's
		self.purchase_invoice = None

		bin = frappe.qb.DocType("Bin")
		item = frappe.qb.DocType("Item")
		query = (
			frappe.qb.from_(bin)
			.join(item)
			.on(item.name == bin.item_code)
			.select(
				bin.item_code,
				item.item_name,
				item.stock_uom,
				item.item_category,
				bin.actual_qty,
				bin.valuation_rate,
			)
			.where(
				(bin.warehouse == self.warehouse)
				& (bin.actual_qty > 0)
				& (item.disabled == 0)
				& (item.is_stock_item == 1)
				& (item.has_batch_no == 0)
				& (item.has_serial_no == 0)
			)
			.orderby(item.item_name)
		)
		if self.item_category:
			query = query.where(item.item_category == self.item_category)
		if self.target_item:
			query = query.where(bin.item_code != self.target_item)

		self.set("items", [])
		for row in query.run(as_dict=True):
			self.append(
				"items",
				{
					"item_code": row.item_code,
					"item_name": row.item_name,
					"uom": row.stock_uom,
					"item_category": row.item_category,
					"warehouse": self.warehouse,
					"available_qty": flt(row.actual_qty),
					"qty": flt(row.actual_qty),
					"valuation_rate": flt(row.valuation_rate),
				},
			)
		self.set_row_targets()
		self.set_totals()

		if not self.items:
			frappe.msgprint(_("No items with stock found in {0}.").format(frappe.bold(self.warehouse)))

	@frappe.whitelist()
	def get_items_from_purchase_invoice(self):
		"""Load the stock items of a submitted Purchase Invoice, one row per item at the qty
		invoiced (summed in stock UOM). The header Warehouse becomes the invoice's warehouse,
		and every row merges there. When an Item Category is set, only that category's
		items are taken."""
		if not self.purchase_invoice:
			frappe.throw(_("Select a Purchase Invoice first."))

		pi = frappe.db.get_value(
			"Purchase Invoice", self.purchase_invoice, ["docstatus", "company", "set_warehouse"], as_dict=True
		)
		if not pi or pi.docstatus != 1:
			frappe.throw(_("Purchase Invoice {0} is not submitted.").format(frappe.bold(self.purchase_invoice)))

		lines = frappe.get_all(
			"Purchase Invoice Item",
			filters={"parent": self.purchase_invoice, "parenttype": "Purchase Invoice"},
			fields=["item_code", "item_name", "stock_uom", "stock_qty", "warehouse"],
			order_by="idx asc",
		)
		self.set_fixed_header()
		self.warehouse = self.warehouse or pi.set_warehouse or next((d.warehouse for d in lines if d.warehouse), None)
		if not self.warehouse:
			frappe.throw(_("Set the Warehouse first."))

		qty_by_item = {}
		for line in lines:
			item = frappe.get_cached_value(
				"Item",
				line.item_code,
				["is_stock_item", "has_batch_no", "has_serial_no", "item_category"],
				as_dict=True,
			)
			if not item or not item.is_stock_item or item.has_batch_no or item.has_serial_no:
				continue
			if self.item_category and item.item_category != self.item_category:
				continue
			if line.item_code == self.target_item:
				continue
			row = qty_by_item.setdefault(
				line.item_code,
				{
					**line,
					"stock_qty": 0,
					"item_category": item.item_category,
				},
			)
			row["stock_qty"] += flt(line.stock_qty)

		sales_invoice = self.source_sales_invoice()
		self.set("items", [])
		for item_code, line in qty_by_item.items():
			bin_row = frappe.db.get_value(
				"Bin",
				{"item_code": item_code, "warehouse": self.warehouse},
				["actual_qty", "valuation_rate"],
				as_dict=True,
			) or frappe._dict()
			available = flt(bin_row.actual_qty)
			self.append(
				"items",
				{
					"sales_invoice": sales_invoice,
					"item_code": item_code,
					"item_name": line["item_name"],
					"uom": line["stock_uom"],
					"item_category": line["item_category"],
					"warehouse": self.warehouse,
					"available_qty": available,
					"invoice_qty": flt(line["stock_qty"]),
					"qty": flt(line["stock_qty"]),
					"valuation_rate": flt(bin_row.valuation_rate),
				},
			)
		self.set_row_targets()
		self.set_totals()

		if not self.items:
			frappe.msgprint(_("No stock items to merge on Purchase Invoice {0}.").format(frappe.bold(self.purchase_invoice)))

	def source_sales_invoice(self):
		"""The Sales Invoice the loaded Purchase Invoice was raised from: dollarj's
		franchise receipt link, ERPNext's inter-company reference, or a bill no that names
		a Sales Invoice."""
		if not self.purchase_invoice:
			return None
		meta = frappe.get_meta("Purchase Invoice")
		fields = [f for f in ("franchise_sales_invoice", "inter_company_invoice_reference", "bill_no") if meta.has_field(f)]
		values = frappe.db.get_value("Purchase Invoice", self.purchase_invoice, fields, as_dict=True) or {}
		for field in fields:
			if values.get(field) and frappe.db.exists("Sales Invoice", values[field]):
				return values[field]
		return None

	def default_target(self, row):
		"""The Item that shares the row's Item Category code, else the tool's Target Item."""
		if row.item_category and frappe.db.exists("Item", row.item_category):
			return row.item_category
		return self.target_item

	def set_row_targets(self):
		for row in self.items:
			row.target_item = self.default_target(row)

	def row_warehouse(self, row):
		"""Every row merges in the header Warehouse."""
		if not self.warehouse:
			frappe.throw(_("Set the Warehouse first."))
		row.warehouse = self.warehouse
		return self.warehouse

	def row_target(self, row):
		"""A row always merges into its Category Merge Item (read-only on screen), so it
		is derived here rather than taken from the client."""
		target = row.target_item = self.default_target(row)
		if not target:
			frappe.throw(
				_("Row #{0}: {1} has no Category Merge Item. Set the Target Item in the header.").format(
					row.idx, frappe.bold(row.item_code)
				)
			)
		return target

	@frappe.whitelist()
	def merge_row(self, row_name):
		"""Merge one row into its own Target Item, leaving the other rows in place."""
		self.set_fixed_header()
		row = next((r for r in self.items if r.name == row_name), None)
		if not row:
			frappe.throw(_("Row not found. Save the tool and try again."))
		return self._merge([row], self.row_target(row), self.row_warehouse(row))

	@frappe.whitelist()
	def make_stock_entry(self):
		"""Merge every row into its own Target Item in its own warehouse: one Repack per
		warehouse and target item. Rows with no qty, or that already are their target item,
		have nothing to merge and are left."""
		self.set_fixed_header()
		groups = {}
		for row in self.items:
			target = self.row_target(row)
			warehouse = self.row_warehouse(row)
			if target == row.item_code or self.locked_qty(row) <= 0:
				continue
			groups.setdefault((warehouse, target), []).append(row)
		if not groups:
			frappe.throw(_("Nothing to merge: no row has a Qty and a Target Item other than itself."))
		return [self._merge(rows, target, warehouse) for (warehouse, target), rows in groups.items()]

	def locked_qty(self, row):
		"""Qty is read-only on screen, so it is set here rather than trusted from the
		client: the qty invoiced when a Purchase Invoice is loaded, else the warehouse stock."""
		if self.purchase_invoice:
			return sum(
				flt(qty)
				for qty in frappe.get_all(
					"Purchase Invoice Item",
					filters={
						"parent": self.purchase_invoice,
						"parenttype": "Purchase Invoice",
						"item_code": row.item_code,
					},
					pluck="stock_qty",
				)
			)
		return flt(
			frappe.db.get_value(
				"Bin", {"item_code": row.item_code, "warehouse": self.row_warehouse(row)}, "actual_qty"
			)
		)

	def _merge(self, rows, target_item, warehouse):
		"""Submit one Repack: the rows go out of their warehouse and the target item comes
		back into it for their total qty, valued at what was consumed."""
		for row in rows:
			row.qty = self.locked_qty(row)
		self.validate()
		empty = [row.item_code for row in rows if flt(row.qty) <= 0]
		rows = [row for row in rows if flt(row.qty) > 0]
		if not rows:
			frappe.throw(
				_("Nothing to merge: no qty of {0} in {1}.").format(
					", ".join(frappe.bold(code) for code in empty), frappe.bold(warehouse)
				)
				if not self.purchase_invoice
				else _("Nothing to merge: {0} is not on Purchase Invoice {1}.").format(
					", ".join(frappe.bold(code) for code in empty), frappe.bold(self.purchase_invoice)
				)
			)

		for row in rows:
			if row.item_code == target_item:
				frappe.throw(
					_("Row #{0}: {1} is the Target Item and cannot be merged into itself.").format(
						row.idx, frappe.bold(row.item_code)
					)
				)
			balance = flt(
				frappe.db.get_value("Bin", {"item_code": row.item_code, "warehouse": warehouse}, "actual_qty")
			)
			if flt(row.qty) > balance:
				frappe.throw(
					_("Row #{0}: {1} {2} is needed but only {3} is in stock in {4}.").format(
						row.idx, flt(row.qty), frappe.bold(row.item_code), balance, frappe.bold(warehouse)
					),
					title=_("Insufficient Stock"),
				)

		se = frappe.new_doc("Stock Entry")
		se.update(
			{
				"stock_entry_type": "Repack",
				"purpose": "Repack",
				"company": frappe.db.get_value("Warehouse", warehouse, "company"),
				"posting_date": self.posting_date,
				"set_posting_time": 1,
				"remarks": _("Category Merge into {0}").format(target_item)
				+ (" " + _("from Purchase Invoice {0}").format(self.purchase_invoice) if self.purchase_invoice else ""),
			}
		)
		for row in rows:
			se.append(
				"items",
				{
					"item_code": row.item_code,
					"s_warehouse": warehouse,
					"qty": flt(row.qty),
					"uom": row.uom,
					"conversion_factor": 1,
				},
			)
		se.append(
			"items",
			{
				"item_code": target_item,
				"t_warehouse": warehouse,
				"qty": sum(flt(row.qty) for row in rows),
				"uom": frappe.db.get_value("Item", target_item, "stock_uom"),
				"conversion_factor": 1,
				"is_finished_item": 1,
			},
		)
		se.insert()
		se.submit()

		merged = {row.name for row in rows}
		self.stock_entry = se.name
		self.set("items", [row for row in self.items if row.name not in merged])
		for idx, row in enumerate(self.items, 1):
			row.idx = idx
		self.set_totals()
		self.save()

		frappe.msgprint(
			_("Stock Entry {0} submitted: {1} merged into {2} in {3}.").format(
				get_link_to_form("Stock Entry", se.name),
				se.items[-1].qty,
				frappe.bold(target_item),
				frappe.bold(warehouse),
			),
			indicator="green",
			alert=True,
		)
		return se.name


@frappe.whitelist()
def get_defaults():
	"""Company and branch warehouse the Category Merge screens open with."""
	warehouse = get_user_branch()
	return {
		"company": DEFAULT_COMPANY if frappe.db.exists("Company", DEFAULT_COMPANY) else None,
		"warehouse": warehouse,
		"warehouse_company": frappe.db.get_value("Warehouse", warehouse, "company") if warehouse else None,
	}


def _tool(company, warehouse, target_item=None, item_category=None, posting_date=None, purchase_invoice=None):
	tool = frappe.get_doc("Category Merge")
	tool.update(
		{
			"company": company,
			"warehouse": warehouse,
			"target_item": target_item,
			"item_category": item_category,
			"posting_date": posting_date or frappe.utils.today(),
			"purchase_invoice": purchase_invoice,
		}
	)
	tool.set_fixed_header()
	return tool


def _rows(tool):
	return [
		{
			"sales_invoice": row.sales_invoice,
			"item_code": row.item_code,
			"item_name": row.item_name,
			"uom": row.uom,
			"item_category": row.item_category,
			"target_item": row.target_item,
			"warehouse": row.warehouse,
			"available_qty": row.available_qty,
			"invoice_qty": row.invoice_qty,
			"qty": row.qty,
			"valuation_rate": row.valuation_rate,
		}
		for row in tool.items
	]


@frappe.whitelist()
def get_merge_items(company, warehouse, item_category=None, target_item=None):
	"""For the X POS Category Merge screen: the rows Get Items would load."""
	frappe.has_permission("Category Merge", "write", throw=True)
	tool = _tool(company, warehouse, target_item, item_category)
	tool.validate()
	tool.get_items()
	return _rows(tool)


@frappe.whitelist()
def get_purchase_invoice_items(purchase_invoice, company=None, warehouse=None, item_category=None, target_item=None):
	"""For the X POS Category Merge screen: the rows Get Items from Purchase Invoice would
	load, plus the company and warehouse it settled on."""
	frappe.has_permission("Category Merge", "write", throw=True)
	frappe.has_permission("Purchase Invoice", "read", doc=purchase_invoice, throw=True)
	tool = _tool(company, warehouse, target_item, item_category, purchase_invoice=purchase_invoice)
	tool.get_items_from_purchase_invoice()
	return {
		"company": tool.company,
		"warehouse": tool.warehouse,
		"sales_invoice": tool.source_sales_invoice(),
		"items": _rows(tool),
	}


@frappe.whitelist(methods=["POST"])
def merge_items(
	company, items, warehouse=None, target_item=None, item_category=None, posting_date=None, purchase_invoice=None
):
	"""For the X POS Category Merge screen: submit one Repack per row Target Item and
	return their names."""
	frappe.has_permission("Category Merge", "write", throw=True)
	tool = _tool(company, warehouse, target_item, item_category, posting_date, purchase_invoice)
	for row in frappe.parse_json(items) or []:
		tool.append(
			"items",
			{
				"item_code": row.get("item_code"),
				"item_name": row.get("item_name"),
				"uom": row.get("uom") or frappe.db.get_value("Item", row.get("item_code"), "stock_uom"),
				"item_category": row.get("item_category"),
				"target_item": row.get("target_item"),
				"warehouse": row.get("warehouse"),
				"invoice_qty": flt(row.get("invoice_qty")),
				"available_qty": flt(row.get("available_qty")),
				"qty": flt(row.get("qty")),
				"valuation_rate": flt(row.get("valuation_rate")),
			},
		)
	return tool.make_stock_entry()


@frappe.whitelist(methods=["POST"])
def merge_row(company, row, warehouse=None, target_item=None, posting_date=None, purchase_invoice=None):
	"""For the X POS Category Merge screen: merge one row into its category's item."""
	frappe.has_permission("Category Merge", "write", throw=True)
	row = frappe.parse_json(row) or {}
	tool = _tool(company, warehouse, target_item, posting_date=posting_date, purchase_invoice=purchase_invoice)
	child = tool.append(
		"items",
		{
			"item_code": row.get("item_code"),
			"item_name": row.get("item_name"),
			"uom": row.get("uom") or frappe.db.get_value("Item", row.get("item_code"), "stock_uom"),
			"item_category": row.get("item_category")
			or frappe.db.get_value("Item", row.get("item_code"), "item_category"),
			"target_item": row.get("target_item"),
			"warehouse": row.get("warehouse"),
			"available_qty": flt(row.get("available_qty")),
			"invoice_qty": flt(row.get("invoice_qty")),
			"qty": flt(row.get("qty")),
			"valuation_rate": flt(row.get("valuation_rate")),
		},
	)
	target = tool.row_target(child)
	warehouse = tool.row_warehouse(child)
	return {
		"stock_entry": tool._merge([child], target, warehouse),
		"target_item": target,
		"warehouse": warehouse,
	}
