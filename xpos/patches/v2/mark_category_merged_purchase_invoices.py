# Copyright (c) 2026, Ali Raza and contributors
# For license information, please see license.txt

import re

import frappe

from xpos.x_pos.doctype.category_merge.category_merge import update_purchase_invoice_merged


def execute():
	"""Link Category Merge Repacks made before the link field existed to their Purchase
	Invoice (named in the remarks), then mark the invoices that are fully merged."""
	# custom/*.json is synced after patches; the two fields are needed now
	from frappe.modules.utils import sync_customizations

	sync_customizations("xpos")

	entries = frappe.get_all(
		"Stock Entry",
		filters={
			"purpose": "Repack",
			"remarks": ["like", "Category Merge into % from Purchase Invoice %"],
			"category_merge_purchase_invoice": ["is", "not set"],
		},
		fields=["name", "remarks"],
	)
	invoices = set()
	for entry in entries:
		match = re.search(r"from Purchase Invoice (\S+)\s*$", entry.remarks or "")
		if not match or not frappe.db.exists("Purchase Invoice", match.group(1)):
			continue
		frappe.db.set_value(
			"Stock Entry",
			entry.name,
			"category_merge_purchase_invoice",
			match.group(1),
			update_modified=False,
		)
		invoices.add(match.group(1))

	for invoice in invoices:
		update_purchase_invoice_merged(invoice)
