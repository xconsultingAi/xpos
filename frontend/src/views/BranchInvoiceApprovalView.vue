<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useMoney } from "@/composables/useMoney";
import { call, showError, showSuccess } from "@/services/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { CheckCheck, RefreshCw, Building2, FileCheck2 } from "lucide-vue-next";
import ConfirmDialog from "@/components/dialogs/ConfirmDialog.vue";
import __ from "@/lib/translate";

const METHOD = "xpos.x_pos.page.branch_invoice_approval.branch_invoice_approval";

interface ApprovalItem {
	item_code: string;
	item_name: string;
	uom: string;
	qty: number;
	rate: number;
	amount: number;
}

interface ApprovalInvoice {
	name: string;
	posting_date: string;
	customer_name: string;
	branch: string;
	workflow_state: string;
	grand_total: number;
	items: ApprovalItem[];
}

interface ApproveResult {
	submitted: string[];
	failed: { name: string; error: string }[];
}

const { money } = useMoney();

const branches = ref<string[]>([]);
const invoices = ref<ApprovalInvoice[]>([]);
const isLoading = ref(false);
const isApproving = ref(false);
const showConfirm = ref(false);
const failures = ref<ApproveResult["failed"]>([]);

const invoiceTotal = (inv: ApprovalInvoice) => inv.items.reduce((sum, it) => sum + (it.amount || 0), 0);
const grandTotal = computed(() => invoices.value.reduce((sum, inv) => sum + invoiceTotal(inv), 0));

async function load(): Promise<void> {
	isLoading.value = true;
	try {
		const data = await call<{ branches: string[]; invoices: ApprovalInvoice[] }>(
			`${METHOD}.get_draft_invoices`,
		);
		branches.value = data?.branches || [];
		invoices.value = data?.invoices || [];
	} catch (e) {
		showError((e as Error)?.message || __("Could not load draft invoices"));
	} finally {
		isLoading.value = false;
	}
}

async function approve(): Promise<void> {
	showConfirm.value = false;
	isApproving.value = true;
	failures.value = [];
	try {
		const result = await call<ApproveResult>(`${METHOD}.approve_invoices`, {
			invoices: invoices.value.map((inv) => inv.name),
		});
		const submitted = result?.submitted || [];
		failures.value = result?.failed || [];
		if (submitted.length) {
			showSuccess(__("{0} invoice(s) approved", [submitted.length]));
		}
		if (failures.value.length) {
			showError(__("{0} invoice(s) could not be approved", [failures.value.length]));
		}
	} catch (e) {
		showError((e as Error)?.message || __("Approval failed"));
	} finally {
		isApproving.value = false;
		await load();
	}
}

function formatDate(date: string): string {
	return date ? new Date(date).toLocaleDateString() : "-";
}

onMounted(load);
</script>

<template>
	<div class="h-full min-h-0 flex flex-col">
		<div class="flex flex-wrap items-center gap-3 px-3 sm:px-4 py-3 border-b border-border">
			<div class="min-w-0 flex-1">
				<h1 class="text-lg font-semibold text-foreground">{{ __("Branch Invoice Approval") }}</h1>
				<p class="text-xs text-muted-foreground flex items-center gap-1">
					<Building2 class="h-3 w-3" />
					<span v-if="branches.length">{{ branches.join(", ") }}</span>
					<span v-else class="text-destructive">{{ __("No branch assigned to you") }}</span>
					<Badge variant="warning" class="ms-2 text-[10px]">
						{{ __("{0} Draft", [invoices.length]) }}
					</Badge>
				</p>
			</div>
			<Button size="sm" variant="outline" :disabled="isLoading" @click="load">
				<RefreshCw class="w-4 h-4" :class="{ 'animate-spin': isLoading }" />
				{{ __("Refresh") }}
			</Button>
			<Button
				size="sm"
				:disabled="!invoices.length || isApproving || isLoading"
				@click="showConfirm = true"
			>
				<CheckCheck class="w-4 h-4" />
				{{ isApproving ? __("Approving...") : __("Approve") }}
			</Button>
		</div>

		<div class="flex-1 min-h-0 overflow-auto px-3 sm:px-4 py-3 xpos-scrollbar">
			<Card v-if="failures.length" class="p-3 mb-3 border-destructive/40">
				<p class="text-sm font-semibold text-destructive mb-1">{{ __("Not approved") }}</p>
				<ul class="text-xs space-y-0.5">
					<li v-for="f in failures" :key="f.name">
						<span class="font-semibold">{{ f.name }}</span>: {{ f.error }}
					</li>
				</ul>
			</Card>

			<div v-if="isLoading && !invoices.length" class="p-8 text-center text-muted-foreground">
				{{ __("Loading...") }}
			</div>

			<div
				v-else-if="!invoices.length"
				class="p-10 text-center text-muted-foreground flex flex-col items-center gap-2"
			>
				<FileCheck2 class="w-10 h-10 opacity-40" />
				{{ __("No draft Sales Invoices for your branch.") }}
			</div>

			<Card v-else class="overflow-x-auto p-0">
				<table class="w-full text-sm">
					<thead class="bg-muted/60 text-muted-foreground text-xs">
						<tr>
							<th class="text-start px-3 py-2">{{ __("Item Code") }}</th>
							<th class="text-start px-3 py-2">{{ __("Item Name") }}</th>
							<th class="text-start px-3 py-2">{{ __("UOM") }}</th>
							<th class="text-end px-3 py-2">{{ __("Qty") }}</th>
							<th class="text-end px-3 py-2">{{ __("Rate") }}</th>
							<th class="text-end px-3 py-2">{{ __("Amount") }}</th>
						</tr>
					</thead>
					<tbody>
						<template v-for="inv in invoices" :key="inv.name">
							<tr class="border-t-2 border-border bg-primary/10">
								<td colspan="6" class="px-3 py-2">
									<div class="flex flex-wrap items-center gap-x-6 gap-y-1">
										<span>
											<span class="text-xs text-muted-foreground">{{ __("Master Invoice No") }}:</span>
											<span class="font-semibold ms-1">{{ inv.name }}</span>
										</span>
										<span>
											<span class="text-xs text-muted-foreground">{{ __("Branch Name") }}:</span>
											<span class="font-semibold ms-1">{{ inv.branch }}</span>
										</span>
										<span class="text-xs text-muted-foreground">
											{{ formatDate(inv.posting_date) }}
										</span>
										<Badge variant="secondary" class="text-[10px] ms-auto">
											{{ __(inv.workflow_state || "Draft") }}
										</Badge>
									</div>
								</td>
							</tr>
							<tr
								v-for="(item, i) in inv.items"
								:key="`${inv.name}-${i}`"
								class="border-t border-border"
							>
								<td class="px-3 py-1.5 ps-6">{{ item.item_code }}</td>
								<td class="px-3 py-1.5">{{ item.item_name }}</td>
								<td class="px-3 py-1.5">{{ item.uom }}</td>
								<td class="px-3 py-1.5 text-end tabular-nums">{{ item.qty }}</td>
								<td class="px-3 py-1.5 text-end tabular-nums">{{ money(item.rate) }}</td>
								<td class="px-3 py-1.5 text-end tabular-nums">{{ money(item.amount) }}</td>
							</tr>
							<tr class="bg-muted/30 font-semibold">
								<td colspan="5" class="px-3 py-1.5 text-end">
									{{ __("Invoice Total") }} - {{ inv.name }}
								</td>
								<td class="px-3 py-1.5 text-end tabular-nums">{{ money(invoiceTotal(inv)) }}</td>
							</tr>
						</template>
					</tbody>
					<tfoot>
						<tr class="border-t-2 border-border font-bold">
							<td colspan="5" class="px-3 py-2 text-end">{{ __("Grand Total") }}</td>
							<td class="px-3 py-2 text-end tabular-nums">{{ money(grandTotal) }}</td>
						</tr>
					</tfoot>
				</table>
			</Card>
		</div>

		<ConfirmDialog
			:open="showConfirm"
			:title="__('Approve Invoices')"
			:description="
				__('Approve all {0} draft invoice(s) shown? They will be received and submitted.', [
					invoices.length,
				])
			"
			:confirm-label="__('Approve')"
			:icon="CheckCheck"
			@confirm="approve"
			@cancel="showConfirm = false"
		/>
	</div>
</template>
