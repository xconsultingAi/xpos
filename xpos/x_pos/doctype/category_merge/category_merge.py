# Copyright (c) 2026, Ali Raza and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_link_to_form


CATEGORY_ITEM_GROUP = "Category"


def create_category_item(category):
	"""Create the Item that stands for a whole Item Category (same code), set up like the
	existing ones (e.g. 900100): stock item in the Category group, in Nos. A merge needs
	it as the Repack target, and the merging branch user may not be allowed to add Items."""
	category_name = frappe.db.get_value("Item Category", category, "category_name") or category
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": category,
			"item_name": category_name,
			"item_group": CATEGORY_ITEM_GROUP
			if frappe.db.exists("Item Group", CATEGORY_ITEM_GROUP)
			else frappe.db.get_single_value("Stock Settings", "item_group"),
			"stock_uom": "Nos",
			"is_stock_item": 1,
			"include_item_in_manufacturing": 1,
			"item_category": category,
		}
	)
	item.flags.ignore_permissions = True
	item.insert()
	return item.name


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


def get_user_company(user=None):
	"""The user's company: their branch warehouse's company, else their POS Profile's,
	else their default company."""
	user = user or frappe.session.user
	branch = get_user_branch(user)
	company = branch and frappe.db.get_value("Warehouse", branch, "company")
	if not company:
		profile = frappe.db.get_value("POS Profile User", {"user": user, "parenttype": "POS Profile"}, "parent")
		company = profile and frappe.db.get_value("POS Profile", profile, "company")
	if not company:
		from erpnext import get_default_company

		company = get_default_company(user)
	return company


class CategoryMerge(Document):
	"""Merge the stock of items, usually one Item Category, into target items with Repack
	Stock Entries. Each row has its own warehouse and target item; the target item is put
	back into the row's warehouse."""

	def validate(self):
		# Company and Warehouse are read-only: the user's company and branch. They are set
		# here so a request cannot point the tool at another company or branch. Each
		# Repack is posted in its warehouse's own company.
		self.set_fixed_header()
		self.set_totals()

	def set_fixed_header(self):
		self.company = get_user_company()
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
		# with a category, list every item of it (as the Item list does), showing 0 where
		# the warehouse holds none; without one, only the items in stock there
		query = (
			frappe.qb.from_(item)
			.left_join(bin)
			.on((bin.item_code == item.name) & (bin.warehouse == self.warehouse))
			.select(
				item.name.as_("item_code"),
				item.item_name,
				item.stock_uom,
				item.item_category,
				bin.actual_qty,
				bin.valuation_rate,
			)
			.where(
				(item.disabled == 0)
				& (item.is_stock_item == 1)
				& (item.has_variants == 0)
				& (item.has_batch_no == 0)
				& (item.has_serial_no == 0)
			)
			.orderby(item.item_name)
		)
		if self.item_category:
			query = query.where(item.item_category == self.item_category)
		else:
			query = query.where(bin.actual_qty > 0)
		if self.target_item:
			query = query.where(item.name != self.target_item)

		self.set("items", [])
		for row in query.run(as_dict=True):
			if flt(row.actual_qty) < 0:
				row.actual_qty = 0
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
			if self.item_category:
				frappe.msgprint(_("No items found in Item Category {0}.").format(frappe.bold(self.item_category)))
			else:
				frappe.msgprint(_("No items with stock found in {0}.").format(frappe.bold(self.warehouse)))

	def invoice_lines(self):
		"""The loaded Purchase Invoice's item lines, each with the warehouse its stock came
		into: the line's own warehouse, else the invoice's, else the header Warehouse."""
		pi = frappe.db.get_value(
			"Purchase Invoice", self.purchase_invoice, ["docstatus", "company", "set_warehouse"], as_dict=True
		)
		if not pi or pi.docstatus != 1:
			frappe.throw(_("Purchase Invoice {0} is not submitted.").format(frappe.bold(self.purchase_invoice)))
		if self.company and pi.company != self.company:
			frappe.throw(
				_("Purchase Invoice {0} belongs to {1}, not your company {2}.").format(
					frappe.bold(self.purchase_invoice), frappe.bold(pi.company), frappe.bold(self.company)
				)
			)
		lines = frappe.get_all(
			"Purchase Invoice Item",
			filters={"parent": self.purchase_invoice, "parenttype": "Purchase Invoice"},
			fields=["item_code", "item_name", "stock_uom", "stock_qty", "warehouse"],
			order_by="idx asc",
		)
		for line in lines:
			line.warehouse = line.warehouse or pi.set_warehouse or self.warehouse
		return lines

	@frappe.whitelist()
	def get_items_from_purchase_invoice(self):
		"""Load the stock items of a submitted Purchase Invoice, one row per item and
		warehouse at the qty invoiced (summed in stock UOM). Each row merges in the
		warehouse the invoice line put its stock into. When an Item Category is set, only
		that category's items are taken."""
		if not self.purchase_invoice:
			frappe.throw(_("Select a Purchase Invoice first."))

		self.set_fixed_header()
		lines = self.invoice_lines()
		if not self.warehouse:
			self.warehouse = next((d.warehouse for d in lines if d.warehouse), None)
		if not any(d.warehouse for d in lines):
			frappe.throw(_("Set the Warehouse first."))

		already_merged = merged_items(self.purchase_invoice)
		qty_by_item = {}
		for line in lines:
			if line.item_code in already_merged:
				continue
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
				(line.item_code, line.warehouse),
				{
					**line,
					"stock_qty": 0,
					"item_category": item.item_category,
				},
			)
			row["stock_qty"] += flt(line.stock_qty)

		sales_invoice = self.source_sales_invoice()
		self.set("items", [])
		for (item_code, warehouse), line in qty_by_item.items():
			bin_row = frappe.db.get_value(
				"Bin",
				{"item_code": item_code, "warehouse": warehouse},
				["actual_qty", "valuation_rate"],
				as_dict=True,
			) or frappe._dict()
			self.append(
				"items",
				{
					"sales_invoice": sales_invoice,
					"item_code": item_code,
					"item_name": line["item_name"],
					"uom": line["stock_uom"],
					"item_category": line["item_category"],
					"warehouse": warehouse,
					"available_qty": flt(bin_row.actual_qty),
					"invoice_qty": flt(line["stock_qty"]),
					"qty": flt(line["stock_qty"]),
					"valuation_rate": flt(bin_row.valuation_rate),
				},
			)
		self.set_row_targets()
		self.set_totals()

		if not self.items:
			if already_merged:
				frappe.msgprint(
					_("Purchase Invoice {0} is already fully merged.").format(frappe.bold(self.purchase_invoice))
				)
			else:
				frappe.msgprint(
					_("No stock items to merge on Purchase Invoice {0}.").format(frappe.bold(self.purchase_invoice))
				)

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
		"""The Item that shares the row's Item Category code (created on merge when it does
		not exist yet), else the tool's Target Item for a row with no category."""
		category = row.item_category
		if category and (
			frappe.db.exists("Item", category) or frappe.db.exists("Item Category", category)
		):
			return category
		return self.target_item

	def set_row_targets(self):
		for row in self.items:
			row.target_item = self.default_target(row)

	def row_warehouse(self, row):
		"""A Purchase Invoice row merges in the warehouse its invoice line put the stock
		into (checked against the invoice, not taken from the client); any other row
		merges in the header Warehouse."""
		if self.purchase_invoice:
			allowed = [d.warehouse for d in self.invoice_lines() if d.item_code == row.item_code and d.warehouse]
			if not allowed:
				frappe.throw(
					_("Row #{0}: {1} is not on Purchase Invoice {2}.").format(
						row.idx, frappe.bold(row.item_code), frappe.bold(self.purchase_invoice)
					)
				)
			row.warehouse = row.warehouse if row.warehouse in allowed else allowed[0]
			return row.warehouse
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
		if target == row.item_category and not frappe.db.exists("Item", target):
			create_category_item(target)
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
			warehouse = self.row_warehouse(row)
			return sum(
				flt(d.stock_qty)
				for d in self.invoice_lines()
				if d.item_code == row.item_code and d.warehouse == warehouse
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

		already_merged = merged_items(self.purchase_invoice) if self.purchase_invoice else set()
		for row in rows:
			if row.item_code in already_merged:
				frappe.throw(
					_("Row #{0}: {1} of Purchase Invoice {2} is already merged.").format(
						row.idx, frappe.bold(row.item_code), frappe.bold(self.purchase_invoice)
					)
				)
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
				"category_merge_purchase_invoice": self.purchase_invoice,
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
		# dollarj blocks Repack Stock Entries for non-Stock-Manager users (so branch users
		# cannot open or edit them); this tool is the controlled way for them to make one
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()
		if self.purchase_invoice:
			update_purchase_invoice_merged(self.purchase_invoice)

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


def merged_items(purchase_invoice):
	"""Items of the Purchase Invoice already merged: the source rows of its submitted
	Category Merge Repacks."""
	if not purchase_invoice:
		return set()
	se = frappe.qb.DocType("Stock Entry")
	sed = frappe.qb.DocType("Stock Entry Detail")
	rows = (
		frappe.qb.from_(sed)
		.join(se)
		.on(sed.parent == se.name)
		.select(sed.item_code)
		.distinct()
		.where(
			(se.category_merge_purchase_invoice == purchase_invoice)
			& (se.docstatus == 1)
			& (sed.s_warehouse.isnotnull())
			& (sed.s_warehouse != "")
		)
		.run(pluck=True)
	)
	return set(rows)


def mergeable_items(purchase_invoice):
	"""Items of the Purchase Invoice the tool can merge: stock items without batch or
	serial numbers that are not themselves a category's item."""
	codes = set(
		frappe.get_all(
			"Purchase Invoice Item",
			filters={"parent": purchase_invoice, "parenttype": "Purchase Invoice"},
			pluck="item_code",
		)
	)
	result = set()
	for code in codes:
		item = frappe.get_cached_value(
			"Item", code, ["is_stock_item", "has_batch_no", "has_serial_no"], as_dict=True
		)
		if not item or not item.is_stock_item or item.has_batch_no or item.has_serial_no:
			continue
		if frappe.db.exists("Item Category", code):
			continue
		result.add(code)
	return result


def update_purchase_invoice_merged(purchase_invoice):
	"""Mark the Purchase Invoice merged once every mergeable item on it is merged, so the
	Category Merge picker stops offering it; unmark it when a merge is cancelled."""
	if not purchase_invoice or not frappe.db.exists("Purchase Invoice", purchase_invoice):
		return
	merged = merged_items(purchase_invoice)
	fully = bool(merged) and mergeable_items(purchase_invoice) <= merged
	frappe.db.set_value(
		"Purchase Invoice", purchase_invoice, "category_merged", int(fully), update_modified=False
	)


def on_stock_entry_cancel(doc, method=None):
	"""A cancelled Category Merge Repack puts its items, and the invoice, back up for merging."""
	if doc.get("category_merge_purchase_invoice"):
		update_purchase_invoice_merged(doc.category_merge_purchase_invoice)


@frappe.whitelist()
def get_defaults():
	"""Company and branch warehouse the Category Merge screens open with."""
	warehouse = get_user_branch()
	return {
		"company": get_user_company(),
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
	if purchase_invoice:
		frappe.has_permission("Purchase Invoice", "read", doc=purchase_invoice, throw=True)
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
	if purchase_invoice:
		frappe.has_permission("Purchase Invoice", "read", doc=purchase_invoice, throw=True)
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
