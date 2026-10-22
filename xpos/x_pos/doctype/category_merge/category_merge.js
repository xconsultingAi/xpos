// Copyright (c) 2026, Ali Raza and contributors
// For license information, please see license.txt

frappe.ui.form.on("Category Merge", {
	setup(frm) {
		frm.set_query("purchase_invoice", () => ({
			filters: {
				docstatus: 1,
				category_merged: 0,
				company: frm.doc.company || "__none__",
			},
		}));
		frm.set_query("target_item", () => ({
			filters: { is_stock_item: 1, disabled: 0, has_variants: 0 },
		}));
	},

	// The user's company and branch warehouse, every time the tool opens
	onload(frm) {
		frappe
			.call("xpos.x_pos.doctype.category_merge.category_merge.get_defaults")
			.then(({ message: d }) => {
				if (!d) return;
				if (d.company) frm.set_value("company", d.company);
				if (d.warehouse) frm.set_value("warehouse", d.warehouse);
				frm.warehouse_company = d.warehouse_company;
			});
	},

	warehouse(frm) {
		frm.warehouse_company = null;
		if (!frm.doc.warehouse) return;
		frappe.db.get_value("Warehouse", frm.doc.warehouse, "company").then(({ message }) => {
			frm.warehouse_company = message && message.company;
		});
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

	// Each Item Category has an Item of the same code standing for the whole category,
	// which is the natural item to merge into.
	// Picking a category also loads its items into the table.
	item_category(frm) {
		const category = frm.doc.item_category;
		if (!category) {
			frm.clear_table("items");
			frm.refresh_fields();
			return;
		}
		frappe.db
			.exists("Item", category)
			.then((exists) => exists && frm.set_value("target_item", category))
			.then(() => {
				// skip a stale reply if the category changed meanwhile
				if (frm.doc.item_category !== category || !frm.doc.warehouse) return;
				return frm
					.call({ doc: frm.doc, method: "get_items", freeze: true })
					.then(() => frm.refresh_fields());
			});
	},

	get_items_from_purchase_invoice(frm) {
		if (!frm.doc.purchase_invoice) {
			frappe.msgprint(__("Select a Purchase Invoice first."));
			return;
		}
		frm.call({ doc: frm.doc, method: "get_items_from_purchase_invoice", freeze: true }).then(() =>
			frm.refresh_fields()
		);
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


	// Merge just this row into its category's item; the other rows stay.
	merge_row(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!flt(row.qty)) {
			frappe.msgprint(__("Row #{0}: set a Qty to merge.", [row.idx]));
			return;
		}
		const target = row.target_item || frm.doc.target_item;
		frappe.confirm(
			__("Merge {0} of {1} into {2} in {3}?", [
				row.qty,
				frappe.bold(row.item_code),
				frappe.bold(target || "-"),
				frappe.bold(frm.doc.warehouse || "-"),
			]),
			() =>
				frm
					.call({
						doc: frm.doc,
						method: "merge_row",
						args: { row_name: row.name },
						freeze: true,
						freeze_message: __("Creating Repack entry..."),
					})
					.then(() => frm.reload_doc())
		);
	},
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
	const missing = rows.find((row) => !row.target_item && !frm.doc.target_item);
	if (missing) {
		frappe.msgprint(__("Row #{0}: set a Target Item.", [missing.idx]));
		return;
	}
	const targets = [...new Set(rows.map((row) => row.target_item || frm.doc.target_item))];
	if (!rows.length) {
		frappe.msgprint(__("Add at least one item with a Qty to merge."));
		return;
	}
	frappe.confirm(
		__("Merge {0} item(s), total qty {1}, into {2}? One Repack per target item.", [
			rows.length,
			frm.doc.total_qty,
			targets.map((t) => frappe.bold(t)).join(", "),
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
