// Copyright (c) 2026, Ali Raza and contributors
// For license information, please see license.txt

frappe.ui.form.on("Category Merge", {
	setup(frm) {
		frm.set_query("warehouse", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));
		frm.set_query("target_item", () => ({
			filters: { is_stock_item: 1, disabled: 0, has_variants: 0 },
		}));
	},

	onload(frm) {
		if (!frm.doc.company) {
			frm.set_value("company", frappe.defaults.get_user_default("Company"));
		}
	},

	refresh(frm) {
		frm.disable_save();
		frm.page.set_primary_action(__("Merge"), () => merge(frm), "merge");
		if (frm.doc.stock_entry) {
			frm.add_custom_button(__("View Last Stock Entry"), () =>
				frappe.set_route("Form", "Stock Entry", frm.doc.stock_entry)
			);
		}
	},

	company(frm) {
		frm.set_value("warehouse", null);
	},

	// Each Item Category has an Item of the same code standing for the whole category,
	// which is the natural item to merge into.
	item_category(frm) {
		if (!frm.doc.item_category) return;
		frappe.db.exists("Item", frm.doc.item_category).then((exists) => {
			if (exists) frm.set_value("target_item", frm.doc.item_category);
		});
	},

	get_items(frm) {
		if (!frm.doc.warehouse) {
			frappe.msgprint(__("Set the Warehouse first."));
			return;
		}
		frm.call({ doc: frm.doc, method: "get_items", freeze: true }).then(() => frm.refresh_fields());
	},
});

frappe.ui.form.on("Category Merge Item", {
	qty: set_totals,
	items_remove: set_totals,
});

function set_totals(frm) {
	let total_qty = 0;
	let total_amount = 0;
	(frm.doc.items || []).forEach((row) => {
		row.amount = flt(row.qty) * flt(row.valuation_rate);
		total_qty += flt(row.qty);
		total_amount += row.amount;
	});
	frm.set_value({ total_qty, total_amount });
	frm.refresh_field("items");
}

function merge(frm) {
	const rows = (frm.doc.items || []).filter((row) => flt(row.qty) > 0);
	if (!frm.doc.target_item) {
		frappe.msgprint(__("Set the Target Item."));
		return;
	}
	if (!rows.length) {
		frappe.msgprint(__("Add at least one item with a Qty to merge."));
		return;
	}
	frappe.confirm(
		__("Merge {0} item(s), total qty {1}, into {2}?", [
			rows.length,
			frm.doc.total_qty,
			frappe.bold(frm.doc.target_item),
		]),
		() =>
			frm
				.call({
					doc: frm.doc,
					method: "make_stock_entry",
					freeze: true,
					freeze_message: __("Creating Repack entry..."),
				})
				.then(() => frm.reload_doc())
	);
}
