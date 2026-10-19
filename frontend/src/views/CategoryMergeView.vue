<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useMoney } from "@/composables/useMoney";
import { usePosStore } from "@/stores/posStore";
import { call, getCount, getValue, showError, showSuccess } from "@/services/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Autocomplete } from "@/components/ui/autocomplete";
import { Combine, ListPlus, Trash2, Boxes, ReceiptText } from "lucide-vue-next";
import ConfirmDialog from "@/components/dialogs/ConfirmDialog.vue";
import __ from "@/lib/translate";

const METHOD = "xpos.x_pos.doctype.category_merge.category_merge";

interface MergeRow {
	sales_invoice?: string;
	item_code: string;
	item_name: string;
	uom: string;
	item_category?: string;
	target_item?: string;
	warehouse?: string;
	available_qty: number;
	invoice_qty?: number;
	qty: number;
	valuation_rate: number;
}

const posStore = usePosStore();
const { money } = useMoney();

const company = ref("");
const warehouse = ref(posStore.warehouse);
const warehouseCompany = ref("");
const itemCategoryName = ref("");
const targetItemName = ref("");

// Dollar Junction and the user's branch warehouse, every time the screen opens
onMounted(async () => {
	try {
		const d = await call<{ company?: string; warehouse?: string; warehouse_company?: string }>(
			`${METHOD}.get_defaults`,
		);
		company.value = d?.company || posStore.companyName;
		warehouse.value = d?.warehouse || posStore.warehouse;
		warehouseCompany.value = d?.warehouse_company || "";
	} catch {
		company.value = posStore.companyName;
	}
});

async function fieldValue(doctype: string, name: string, field: string): Promise<string> {
	if (!name) return "";
	try {
		const row = await getValue<Record<string, string>>(doctype, name, [field]);
		return row?.[field] || "";
	} catch {
		return "";
	}
}

const itemCategory = ref("");
const targetItem = ref("");
const purchaseInvoice = ref("");
const salesInvoiceNo = ref("");

watch(warehouse, async (wh) => {
	warehouseCompany.value = await fieldValue("Warehouse", wh, "company");
});
watch(targetItem, async (item) => {
	targetItemName.value = await fieldValue("Item", item, "item_name");
});
const rows = ref<MergeRow[]>([]);
const isLoading = ref(false);
const isMerging = ref(false);
const showConfirm = ref(false);
const lastStockEntry = ref("");

const mergeRows = computed(() => rows.value.filter((r) => Number(r.qty) > 0));
const totalQty = computed(() => mergeRows.value.reduce((s, r) => s + Number(r.qty), 0));
const totalAmount = computed(() =>
	mergeRows.value.reduce((s, r) => s + Number(r.qty) * (r.valuation_rate || 0), 0),
);
const rowTarget = (row: MergeRow) => row.target_item || targetItem.value;
const mergeTargets = computed(() => [...new Set(mergeRows.value.map(rowTarget).filter(Boolean))]);
const canMerge = computed(
	() =>
		!!(company.value && warehouse.value && mergeRows.value.length) &&
		mergeRows.value.every((row) => !!rowTarget(row)),
);
const mergingRow = ref("");

// Each Item Category has an Item of the same code standing for the whole category,
// which is the natural item to merge into.
watch(itemCategory, async (category) => {
	itemCategoryName.value = await fieldValue("Item Category", category, "category_name");
	rows.value = [];
	if (!category) return;
	try {
		if (await getCount("Item", { name: category })) targetItem.value = category;
	} catch {
		/* leave the target as it is */
	}
});

async function getItems(): Promise<void> {
	if (!company.value || !warehouse.value) {
		showError(__("Set the Company and Warehouse first."));
		return;
	}
	isLoading.value = true;
	purchaseInvoice.value = "";
	salesInvoiceNo.value = "";
	try {
		rows.value =
			(await call<MergeRow[]>(`${METHOD}.get_merge_items`, {
				company: company.value,
				warehouse: warehouse.value,
				item_category: itemCategory.value || null,
				target_item: targetItem.value || null,
			})) || [];
		if (!rows.value.length) showError(__("No items with stock found in {0}.", [warehouse.value]));
	} catch (e) {
		showError((e as Error)?.message || __("Could not load items"));
	} finally {
		isLoading.value = false;
	}
}

async function getItemsFromPurchaseInvoice(): Promise<void> {
	if (!purchaseInvoice.value) {
		showError(__("Select a Purchase Invoice first."));
		return;
	}
	isLoading.value = true;
	try {
		const data = await call<{
			company: string;
			warehouse: string;
			sales_invoice?: string;
			items: MergeRow[];
		}>(
			`${METHOD}.get_purchase_invoice_items`,
			{
				purchase_invoice: purchaseInvoice.value,
				company: company.value || null,
				warehouse: warehouse.value || null,
				item_category: itemCategory.value || null,
				target_item: targetItem.value || null,
			},
		);
		company.value = data?.company || company.value;
		warehouse.value = data?.warehouse || warehouse.value;
		salesInvoiceNo.value = data?.sales_invoice || "";
		rows.value = data?.items || [];
		if (!rows.value.length) {
			showError(__("No stock items to merge on Purchase Invoice {0}.", [purchaseInvoice.value]));
		}
	} catch (e) {
		showError((e as Error)?.message || __("Could not load the Purchase Invoice items"));
	} finally {
		isLoading.value = false;
	}
}

// Merge just this row into its category's item; the other rows stay on screen.
async function mergeRow(index: number): Promise<void> {
	const row = rows.value[index];
	if (!(Number(row.qty) > 0)) {
		showError(__("Set a Qty to merge {0}.", [row.item_code]));
		return;
	}
	mergingRow.value = row.item_code;
	try {
		const result = await call<{ stock_entry: string; target_item: string; warehouse: string }>(
			`${METHOD}.merge_row`,
			{
				company: company.value,
				warehouse: warehouse.value,
				target_item: targetItem.value || null,
				purchase_invoice: purchaseInvoice.value || null,
				row: JSON.stringify(row),
			},
		);
		lastStockEntry.value = result.stock_entry;
		showSuccess(
			__("{0} merged into {1} in {2} ({3})", [
				row.item_code,
				result.target_item,
				result.warehouse,
				result.stock_entry,
			]),
		);
		rows.value.splice(index, 1);
	} catch (e) {
		showError((e as Error)?.message || __("Merge failed"));
	} finally {
		mergingRow.value = "";
	}
}

function removeRow(index: number): void {
	rows.value.splice(index, 1);
}

async function merge(): Promise<void> {
	showConfirm.value = false;
	isMerging.value = true;
	try {
		const names = await call<string[]>(`${METHOD}.merge_items`, {
			company: company.value,
			warehouse: warehouse.value,
			item_category: itemCategory.value || null,
			target_item: targetItem.value || null,
			purchase_invoice: purchaseInvoice.value || null,
			items: JSON.stringify(mergeRows.value),
		});
		lastStockEntry.value = names[names.length - 1] || "";
		showSuccess(__("{0} Stock Entry(s) submitted: {1}", [names.length, names.join(", ")]));
		rows.value = [];
	} catch (e) {
		showError((e as Error)?.message || __("Merge failed"));
	} finally {
		isMerging.value = false;
	}
}
</script>

<template>
	<div class="h-full min-h-0 flex flex-col">
		<div class="flex flex-wrap items-center gap-3 px-3 sm:px-4 py-3 border-b border-border">
			<div class="min-w-0 flex-1">
				<h1 class="text-lg font-semibold text-foreground">{{ __("Category Merge") }}</h1>
				<p class="text-xs text-muted-foreground">
					{{ __("Repack the stock of a category's items into one target item") }}
					<span v-if="lastStockEntry" class="ms-2">
						· {{ __("Last Stock Entry") }}:
						<a
							:href="`/app/stock-entry/${encodeURIComponent(lastStockEntry)}`"
							target="_blank"
							class="font-semibold text-primary underline"
							>{{ lastStockEntry }}</a
						>
					</span>
				</p>
			</div>
			<Button size="sm" :disabled="!canMerge || isMerging" @click="showConfirm = true">
				<Combine class="w-4 h-4" />
				{{ isMerging ? __("Merging...") : __("Merge") }}
			</Button>
		</div>

		<div class="flex-1 min-h-0 overflow-auto px-3 sm:px-4 py-3 xpos-scrollbar space-y-3">
			<Card class="p-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
				<div>
					<label class="text-xs text-muted-foreground">{{ __("Company") }}</label>
					<div
						class="h-8 px-3 flex items-center rounded-md border border-input bg-muted/50 text-sm font-semibold truncate cursor-not-allowed"
						:title="__('Read only')"
					>
						{{ company || "-" }}
					</div>
				</div>
				<div>
					<label class="text-xs text-muted-foreground">{{ __("Warehouse") }}</label>
					<div
						class="h-8 px-3 flex items-center rounded-md border border-input bg-muted/50 text-sm font-semibold truncate cursor-not-allowed"
						:title="__('Read only')"
					>
						{{ warehouse || "-" }}
					</div>
				</div>
				<div>
					<label class="text-xs text-muted-foreground">{{ __("Item Category") }}</label>
					<Autocomplete
						v-model="itemCategory"
						doctype="Item Category"
						:placeholder="__('Item Category')"
						class="h-8 text-sm"
					/>
					<p v-if="itemCategoryName" class="text-xs text-muted-foreground mt-1 truncate">
						{{ itemCategoryName }}
					</p>
				</div>
				<div>
					<label class="text-xs text-muted-foreground">{{ __("Target Item") }}</label>
					<Autocomplete
						v-model="targetItem"
						doctype="Item"
						:filters="{ is_stock_item: 1, disabled: 0, has_variants: 0 }"
						:placeholder="__('Target Item')"
						class="h-8 text-sm"
					/>
					<p v-if="targetItemName" class="text-xs text-muted-foreground mt-1 truncate">
						{{ targetItemName }}
					</p>
				</div>
				<div class="sm:col-span-2 lg:col-span-4 flex justify-end">
					<Button size="sm" variant="outline" :disabled="isLoading" @click="getItems">
						<ListPlus class="w-4 h-4" />
						{{ isLoading ? __("Loading...") : __("Get Items") }}
					</Button>
				</div>
			</Card>

			<Card class="p-3">
				<p class="text-sm font-semibold mb-2 flex items-center gap-1.5">
					<ReceiptText class="w-4 h-4" />
					{{ __("Purchase Invoice") }}
				</p>
				<div class="flex flex-wrap items-end gap-3">
					<div class="flex-1 min-w-56">
						<label class="text-xs text-muted-foreground">
							{{ __("Load the items of a submitted Purchase Invoice to merge") }}
						</label>
						<Autocomplete
							v-model="purchaseInvoice"
							doctype="Purchase Invoice"
							:filters="{
								docstatus: 1,
								...(warehouseCompany ? { company: warehouseCompany } : {}),
							}"
							:placeholder="__('Purchase Invoice')"
							class="h-8 text-sm"
						/>
					</div>
					<Button
						size="sm"
						variant="outline"
						:disabled="isLoading || !purchaseInvoice"
						@click="getItemsFromPurchaseInvoice"
					>
						<ListPlus class="w-4 h-4" />
						{{ __("Get Items from Purchase Invoice") }}
					</Button>
				</div>
			</Card>

			<div
				v-if="!rows.length"
				class="p-10 text-center text-muted-foreground flex flex-col items-center gap-2"
			>
				<Boxes class="w-10 h-10 opacity-40" />
				{{ __("Pick a warehouse and category, then Get Items, or load a Purchase Invoice.") }}
			</div>

			<Card v-else class="overflow-x-auto p-0">
				<div
					class="flex flex-wrap items-center gap-x-8 gap-y-1 px-3 py-2 bg-primary/10 border-b border-border text-sm"
				>
					<span>
						<span class="text-xs text-muted-foreground">{{ __("Purchase Invoice No") }}:</span>
						<span class="font-semibold ms-1">{{ purchaseInvoice || "-" }}</span>
					</span>
					<span>
						<span class="text-xs text-muted-foreground">{{ __("Sales Invoice No") }}:</span>
						<span class="font-semibold ms-1">{{ salesInvoiceNo || "-" }}</span>
					</span>
					<span>
						<span class="text-xs text-muted-foreground">{{ __("Warehouse Name") }}:</span>
						<span class="font-semibold ms-1">{{ warehouse || "-" }}</span>
					</span>
				</div>
				<table class="w-full text-sm">
					<thead class="bg-muted/60 text-muted-foreground text-xs">
						<tr>
							<th class="text-start px-3 py-2">{{ __("Sales Invoice No") }}</th>
							<th class="text-start px-3 py-2">{{ __("Item Code") }}</th>
							<th class="text-start px-3 py-2">{{ __("Item Name") }}</th>
							<th class="text-start px-3 py-2">{{ __("UOM") }}</th>
							<th class="text-start px-3 py-2">{{ __("Item Category") }}</th>
							<th class="text-start px-3 py-2">{{ __("Category Merge Item") }}</th>
							<th class="text-end px-3 py-2">{{ __("Invoice Qty") }}</th>
							<th class="text-end px-3 py-2 w-28">{{ __("Qty") }}</th>
							<th class="text-end px-3 py-2">{{ __("Valuation Rate") }}</th>
							<th class="text-end px-3 py-2">{{ __("Amount") }}</th>
							<th class="w-28"></th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="(row, i) in rows" :key="row.item_code" class="border-t border-border">
							<td class="px-3 py-1.5">{{ row.sales_invoice || "-" }}</td>
							<td class="px-3 py-1.5">{{ row.item_code }}</td>
							<td class="px-3 py-1.5">{{ row.item_name }}</td>
							<td class="px-3 py-1.5">{{ row.uom }}</td>
							<td class="px-3 py-1.5">{{ row.item_category || "-" }}</td>
							<td class="px-3 py-1.5 font-semibold">{{ rowTarget(row) || "-" }}</td>
							<td class="px-3 py-1.5 text-end tabular-nums">
								{{ row.invoice_qty ?? "-" }}
							</td>
							<td class="px-3 py-1.5 text-end tabular-nums font-semibold">{{ row.qty }}</td>
							<td class="px-3 py-1.5 text-end tabular-nums">{{ money(row.valuation_rate) }}</td>
							<td class="px-3 py-1.5 text-end tabular-nums">
								{{ money(Number(row.qty) * (row.valuation_rate || 0)) }}
							</td>
							<td class="px-2 py-1 text-center whitespace-nowrap">
								<Button
									size="sm"
									variant="outline"
									class="h-7 px-2 me-1"
									:disabled="!!mergingRow || isMerging"
									:title="__('Merge into {0}', [rowTarget(row) || '-'])"
									@click="mergeRow(i)"
								>
									<Combine class="w-3.5 h-3.5" />
									{{ mergingRow === row.item_code ? __("Merging...") : __("Merge") }}
								</Button>
								<Button variant="ghost" size="icon" class="h-7 w-7" @click="removeRow(i)">
									<Trash2 class="w-4 h-4 text-destructive" />
								</Button>
							</td>
						</tr>
					</tbody>
					<tfoot>
						<tr class="border-t-2 border-border font-bold">
							<td colspan="7" class="px-3 py-2 text-end">
								{{ __("Total") }} → {{ mergeTargets.join(", ") || "-" }}
							</td>
							<td class="px-3 py-2 text-end tabular-nums">{{ totalQty }}</td>
							<td></td>
							<td class="px-3 py-2 text-end tabular-nums">{{ money(totalAmount) }}</td>
							<td></td>
						</tr>
					</tfoot>
				</table>
			</Card>
		</div>

		<ConfirmDialog
			:open="showConfirm"
			:title="__('Merge Items')"
			:description="
				__('Merge {0} item(s), total qty {1}, into {2}? One Repack per target item.', [
					mergeRows.length,
					totalQty,
					mergeTargets.join(', '),
				])
			"
			:confirm-label="__('Merge')"
			:icon="Combine"
			@confirm="merge"
			@cancel="showConfirm = false"
		/>
	</div>
</template>
