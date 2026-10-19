# Copyright (c) 2026, Ali Raza and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_link_to_form


class CategoryMerge(Document):
	"""Merge the stock of several items, usually one Item Category, into a single target
	item with one Repack Stock Entry in the same warehouse."""

	def validate(self):
		if self.warehouse and self.company:
			if frappe.db.get_value("Warehouse", self.warehouse, "company") != self.company:
				frappe.throw(
					_("Warehouse {0} does not belong to company {1}.").format(
						frappe.bold(self.warehouse), frappe.bold(self.company)
					)
				)
		self.set_totals()

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
		if not self.warehouse:
			frappe.throw(_("Set the Warehouse first."))

		bin = frappe.qb.DocType("Bin")
		item = frappe.qb.DocType("Item")
		query = (
			frappe.qb.from_(bin)
			.join(item)
			.on(item.name == bin.item_code)
			.select(bin.item_code, item.item_name, item.stock_uom, bin.actual_qty, bin.valuation_rate)
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
					"available_qty": flt(row.actual_qty),
					"qty": flt(row.actual_qty),
					"valuation_rate": flt(row.valuation_rate),
				},
			)
		self.set_totals()

		if not self.items:
			frappe.msgprint(_("No items with stock found in {0}.").format(frappe.bold(self.warehouse)))

	@frappe.whitelist()
	def make_stock_entry(self):
		"""Submit one Repack: every row goes out of the warehouse and the target item comes
		back in for the total qty, valued at what was consumed."""
		self.validate()
		rows = [row for row in self.items if flt(row.qty) > 0]
		if not rows:
			frappe.throw(_("Add at least one item with a Qty to merge."))
		if not self.target_item:
			frappe.throw(_("Set the Target Item."))

		for row in rows:
			if row.item_code == self.target_item:
				frappe.throw(
					_("Row #{0}: {1} is the Target Item and cannot be merged into itself.").format(
						row.idx, frappe.bold(row.item_code)
					)
				)
			balance = flt(
				frappe.db.get_value("Bin", {"item_code": row.item_code, "warehouse": self.warehouse}, "actual_qty")
			)
			if flt(row.qty) > balance:
				frappe.throw(
					_("Row #{0}: Only {1} of {2} is in stock in {3}.").format(
						row.idx, balance, frappe.bold(row.item_code), frappe.bold(self.warehouse)
					)
				)

		se = frappe.new_doc("Stock Entry")
		se.update(
			{
				"stock_entry_type": "Repack",
				"purpose": "Repack",
				"company": self.company,
				"posting_date": self.posting_date,
				"set_posting_time": 1,
				"remarks": _("Category Merge into {0}").format(self.target_item),
			}
		)
		for row in rows:
			se.append(
				"items",
				{
					"item_code": row.item_code,
					"s_warehouse": self.warehouse,
					"qty": flt(row.qty),
					"uom": row.uom,
					"conversion_factor": 1,
				},
			)
		target_uom = frappe.db.get_value("Item", self.target_item, "stock_uom")
		se.append(
			"items",
			{
				"item_code": self.target_item,
				"t_warehouse": self.warehouse,
				"qty": self.total_qty,
				"uom": target_uom,
				"conversion_factor": 1,
				"is_finished_item": 1,
			},
		)
		se.insert()
		se.submit()

		self.stock_entry = se.name
		self.set("items", [])
		self.set_totals()
		self.save()

		frappe.msgprint(
			_("Stock Entry {0} submitted: {1} merged into {2}.").format(
				get_link_to_form("Stock Entry", se.name), se.items[-1].qty, frappe.bold(self.target_item)
			),
			indicator="green",
			alert=True,
		)
		return se.name


def _tool(company, warehouse, target_item=None, item_category=None, posting_date=None):
	tool = frappe.get_doc("Category Merge")
	tool.update(
		{
			"company": company,
			"warehouse": warehouse,
			"target_item": target_item,
			"item_category": item_category,
			"posting_date": posting_date or frappe.utils.today(),
		}
	)
	return tool


@frappe.whitelist()
def get_merge_items(company, warehouse, item_category=None, target_item=None):
	"""For the X POS Category Merge screen: the rows Get Items would load."""
	frappe.has_permission("Category Merge", "write", throw=True)
	tool = _tool(company, warehouse, target_item, item_category)
	tool.validate()
	tool.get_items()
	return [
		{
			"item_code": row.item_code,
			"item_name": row.item_name,
			"uom": row.uom,
			"available_qty": row.available_qty,
			"qty": row.qty,
			"valuation_rate": row.valuation_rate,
		}
		for row in tool.items
	]


@frappe.whitelist(methods=["POST"])
def merge_items(company, warehouse, target_item, items, item_category=None, posting_date=None):
	"""For the X POS Category Merge screen: submit the Repack and return its name."""
	frappe.has_permission("Category Merge", "write", throw=True)
	tool = _tool(company, warehouse, target_item, item_category, posting_date)
	for row in frappe.parse_json(items) or []:
		tool.append(
			"items",
			{
				"item_code": row.get("item_code"),
				"item_name": row.get("item_name"),
				"uom": row.get("uom") or frappe.db.get_value("Item", row.get("item_code"), "stock_uom"),
				"available_qty": flt(row.get("available_qty")),
				"qty": flt(row.get("qty")),
				"valuation_rate": flt(row.get("valuation_rate")),
			},
		)
	return tool.make_stock_entry()
