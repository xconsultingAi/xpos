// Copyright (c) 2026, Ali Raza and contributors
// For license information, please see license.txt

const BIA_METHOD = "xpos.x_pos.page.branch_invoice_approval.branch_invoice_approval";

frappe.pages["branch-invoice-approval"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Branch Invoice Approval"),
		single_column: true,
	});
	wrapper.bia = new BranchInvoiceApproval(page);
};

frappe.pages["branch-invoice-approval"].on_page_show = function (wrapper) {
	wrapper.bia && wrapper.bia.load();
};

class BranchInvoiceApproval {
	constructor(page) {
		this.page = page;
		this.invoices = [];
		this.$body = $(`<div class="bia-body"></div>`).appendTo(page.main);

		this.approve_btn = page.set_primary_action(__("Approve"), () => this.approve(), "check");
		page.set_secondary_action(__("Refresh"), () => this.load(), "refresh");
	}

	load() {
		this.$body.html(`<div class="text-muted p-4">${__("Loading...")}</div>`);
		frappe.call({
			method: `${BIA_METHOD}.get_draft_invoices`,
			callback: (r) => {
				const data = r.message || {};
				this.invoices = data.invoices || [];
				this.render(data.branches || []);
			},
		});
	}

	render(branches) {
		const branch_label = branches.length
			? branches.map((b) => frappe.utils.escape_html(b)).join(", ")
			: `<span class="text-danger">${__("No branch assigned to you")}</span>`;

		this.page.set_indicator(
			__("{0} Draft", [this.invoices.length]),
			this.invoices.length ? "orange" : "green"
		);
		this.approve_btn.prop("disabled", !this.invoices.length);

		let html = `<div class="mb-3"><b>${__("Branch")}:</b> ${branch_label}</div>`;

		if (!this.invoices.length) {
			html += `<div class="text-muted p-4 text-center border rounded">${__(
				"No draft Sales Invoices for your branch."
			)}</div>`;
			this.$body.html(html);
			return;
		}

		const fmt_cur = (v) => format_currency(v);
		const fmt_qty = (v) => format_number(v, null, 2);
		let total = 0;

		html += `<div class="table-responsive"><table class="table table-bordered table-sm bia-table">
			<thead><tr>
				<th>${__("Master Invoice No")}</th>
				<th>${__("Branch Name")}</th>
				<th>${__("Item Code")}</th>
				<th>${__("Item Name")}</th>
				<th>${__("UOM")}</th>
				<th class="text-right">${__("Qty")}</th>
				<th class="text-right">${__("Rate")}</th>
				<th class="text-right">${__("Amount")}</th>
			</tr></thead><tbody>`;

		for (const inv of this.invoices) {
			const esc = frappe.utils.escape_html;
			const link = `<a href="/app/sales-invoice/${encodeURIComponent(inv.name)}">${esc(
				inv.name
			)}</a>`;
			let inv_amount = 0;

			inv.items.forEach((item, i) => {
				inv_amount += item.amount;
				html += `<tr>
					<td>${i === 0 ? link : ""}</td>
					<td>${i === 0 ? esc(inv.branch || "") : ""}</td>
					<td>${esc(item.item_code || "")}</td>
					<td>${esc(item.item_name || "")}</td>
					<td>${esc(item.uom || "")}</td>
					<td class="text-right">${fmt_qty(item.qty)}</td>
					<td class="text-right">${fmt_cur(item.rate)}</td>
					<td class="text-right">${fmt_cur(item.amount)}</td>
				</tr>`;
			});

			total += inv_amount;
			html += `<tr class="bia-subtotal">
				<td colspan="7" class="text-right">
					<span class="text-muted mr-2">${esc(__(inv.workflow_state || "Draft"))}</span>
					${__("Invoice Total")} - ${esc(inv.name)}
				</td>
				<td class="text-right">${fmt_cur(inv_amount)}</td>
			</tr>`;
		}

		html += `</tbody><tfoot><tr class="bia-total">
				<td colspan="7" class="text-right">${__("Grand Total")}</td>
				<td class="text-right">${fmt_cur(total)}</td>
			</tr></tfoot></table></div>
			<style>
				.bia-table th { background: var(--subtle-fg); }
				.bia-subtotal td { background: var(--subtle-accent); font-weight: 600; }
				.bia-total td { font-weight: 700; font-size: var(--text-md); }
			</style>`;

		this.$body.html(html);
	}

	approve() {
		const names = this.invoices.map((inv) => inv.name);
		if (!names.length) return;

		frappe.confirm(
			__("Approve all {0} draft invoice(s) shown? They will be received and submitted.", [names.length]),
			() => {
				frappe.call({
					method: `${BIA_METHOD}.approve_invoices`,
					args: { invoices: names },
					freeze: true,
					freeze_message: __("Approving invoices..."),
					callback: (r) => {
						const { submitted = [], failed = [] } = r.message || {};
						if (failed.length) {
							const rows = failed
								.map(
									(f) =>
										`<li><b>${frappe.utils.escape_html(f.name)}</b>: ${frappe.utils.escape_html(
											f.error
										)}</li>`
								)
								.join("");
							frappe.msgprint({
								title: __("{0} approved, {1} failed", [
									submitted.length,
									failed.length,
								]),
								message: `<ul>${rows}</ul>`,
								indicator: "orange",
							});
						} else {
							frappe.show_alert({
								message: __("{0} invoice(s) approved", [submitted.length]),
								indicator: "green",
							});
						}
						this.load();
					},
				});
			}
		);
	}
}
