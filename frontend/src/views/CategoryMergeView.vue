<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMoney } from "@/composables/useMoney";
import { usePosStore } from "@/stores/posStore";
import { call, getCount, showError, showSuccess } from "@/services/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Autocomplete } from "@/components/ui/autocomplete";
import { Combine, ListPlus, Trash2, Boxes } from "lucide-vue-next";
import ConfirmDialog from "@/components/dialogs/ConfirmDialog.vue";
import __ from "@/lib/translate";

const METHOD = "xpos.x_pos.doctype.category_merge.category_merge";

interface MergeRow {
	item_code: string;
	item_name: string;
	uom: string;
	available_qty: number;
	qty: number;
	valuation_rate: number;
}

const posStore = usePosStore();
const { money } = useMoney();

const company = ref(posStore.companyName);
const warehouse = ref(posStore.warehouse);
const itemCategory = ref("");
const targetItem = ref("");
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
const canMerge = computed(
	() => !!(company.value && warehouse.value && targetItem.value && mergeRows.value.length),
);

// Each Item Category has an Item of the same code standing for the whole category,
// which is the natural item to merge into.
watch(itemCategory, async (category) => {
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

function clampQty(row: MergeRow): void {
	const qty = Number(row.qty) || 0;
	row.qty = Math.min(Math.max(qty, 0), row.available_qty);
}

function removeRow(index: number): void {
	rows.value.splice(index, 1);
}

async function merge(): Promise<void> {
	showConfirm.value = false;
	isMerging.value = true;
	try {
		const name = await call<string>(`${METHOD}.merge_items`, {
			company: company.value,
			warehouse: warehouse.value,
			item_category: itemCategory.value || null,
			target_item: targetItem.value,
			items: JSON.stringify(mergeRows.value),
		});
		lastStockEntry.value = name;
		showSuccess(__("Stock Entry {0} submitted", [name]));
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
					<Autocomplete v-model="company" doctype="Company" :placeholder="__('Company')" class="h-8 text-sm" />
				</div>
				<div>
					<label class="text-xs text-muted-foreground">{{ __("Warehouse") }}</label>
					<Autocomplete
						v-model="warehouse"
						doctype="Warehouse"
						:filters="{ company, is_group: 0 }"
						:placeholder="__('Warehouse')"
						class="h-8 text-sm"
					/>
				</div>
				<div>
					<label class="text-xs text-muted-foreground">{{ __("Item Category") }}</label>
					<Autocomplete
						v-model="itemCategory"
						doctype="Item Category"
						:placeholder="__('Item Category')"
						class="h-8 text-sm"
					/>
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
				</div>
				<div class="sm:col-span-2 lg:col-span-4 flex justify-end">
					<Button size="sm" variant="outline" :disabled="isLoading" @click="getItems">
						<ListPlus class="w-4 h-4" />
						{{ isLoading ? __("Loading...") : __("Get Items") }}
					</Button>
				</div>
			</Card>

			<div
				v-if="!rows.length"
				class="p-10 text-center text-muted-foreground flex flex-col items-center gap-2"
			>
				<Boxes class="w-10 h-10 opacity-40" />
				{{ __("Pick a warehouse and category, then Get Items.") }}
			</div>

			<Card v-else class="overflow-x-auto p-0">
				<table class="w-full text-sm">
					<thead class="bg-muted/60 text-muted-foreground text-xs">
						<tr>
							<th class="text-start px-3 py-2">{{ __("Item Code") }}</th>
							<th class="text-start px-3 py-2">{{ __("Item Name") }}</th>
							<th class="text-start px-3 py-2">{{ __("UOM") }}</th>
							<th class="text-end px-3 py-2">{{ __("Available Qty") }}</th>
							<th class="text-end px-3 py-2 w-28">{{ __("Qty") }}</th>
							<th class="text-end px-3 py-2">{{ __("Valuation Rate") }}</th>
							<th class="text-end px-3 py-2">{{ __("Amount") }}</th>
							<th class="w-10"></th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="(row, i) in rows" :key="row.item_code" class="border-t border-border">
							<td class="px-3 py-1.5">{{ row.item_code }}</td>
							<td class="px-3 py-1.5">{{ row.item_name }}</td>
							<td class="px-3 py-1.5">{{ row.uom }}</td>
							<td class="px-3 py-1.5 text-end tabular-nums">{{ row.available_qty }}</td>
							<td class="px-3 py-1">
								<Input
									v-model.number="row.qty"
									type="number"
									min="0"
									:max="row.available_qty"
									class="h-7 text-end tabular-nums"
									@blur="clampQty(row)"
								/>
							</td>
							<td class="px-3 py-1.5 text-end tabular-nums">{{ money(row.valuation_rate) }}</td>
							<td class="px-3 py-1.5 text-end tabular-nums">
								{{ money(Number(row.qty) * (row.valuation_rate || 0)) }}
							</td>
							<td class="px-2 py-1 text-center">
								<Button variant="ghost" size="icon" class="h-7 w-7" @click="removeRow(i)">
									<Trash2 class="w-4 h-4 text-destructive" />
								</Button>
							</td>
						</tr>
					</tbody>
					<tfoot>
						<tr class="border-t-2 border-border font-bold">
							<td colspan="4" class="px-3 py-2 text-end">
								{{ __("Total") }} → {{ targetItem || "-" }}
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
				__('Merge {0} item(s), total qty {1}, into {2}?', [mergeRows.length, totalQty, targetItem])
			"
			:confirm-label="__('Merge')"
			:icon="Combine"
			@confirm="merge"
			@cancel="showConfirm = false"
		/>
	</div>
</template>
