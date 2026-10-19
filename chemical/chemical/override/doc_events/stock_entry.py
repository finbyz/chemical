import frappe
from frappe import _
from frappe.utils import nowdate, flt, cint
from chemical.chemical.override.utils import (
	se_cal_rate_qty,
	se_repack_cal_rate_qty,
	cal_actual_valuations,
)
from chemical.chemical.override.batch_valuation import (
	stock_entry_validate as batch_valuation_stock_entry_validate,
	set_incoming_rate,
	stock_entry_on_cancel as batch_valuation_stock_entry_on_cancel,
)

from chemical.chemical.override.doctype.stock_entry import StockEntry


def before_validate(self, method):
	update_item_batches_based_on_fifo(self)
	stock_entry_calculate_rate_qty(self)
	fg_completed_quantity_to_fg_completed_qty(self)
	cal_actual_valuations(self)
	self.validate_fg_completed_qty()


def validate(self, method):
	stock_entry_validate(self)
	batch_valuation_stock_entry_validate(self)
	check_based_on_item(self)
	cal_validate_additional_cost_qty(self)
	calculate_rate_and_amount(self)
	get_based_on(self)
	cal_target_yield_cons(self)


def before_save(self, method):
	if self.purpose == "Repack" and cint(self.from_ball_mill) != 1:
		self.get_stock_and_rate()


def before_submit(self, method):
	validate_concentration(self)
	validate_quality_inspection(self)


def on_submit(self, method):
	update_po(self)
	set_batch_qc(self)


def before_cancel(self, method):
	StockEntry.delete_auto_created_batches = delete_auto_created_batches
	if self.work_order:
		wo = frappe.get_doc("Work Order", self.work_order)
		wo.db_set("batch", "")

	if self.work_order:
		pro_doc = frappe.get_doc("Work Order", self.work_order)
		pro_doc.db_set("batch", "")
		set_po_status(self, pro_doc)
		update_po_volume(self, pro_doc)
		pro_doc.save()


def on_cancel(self, method):
	if self.work_order:
		pro_doc = frappe.get_doc("Work Order", self.work_order)
		set_po_status(self, pro_doc)			
		update_po_transfer_qty(self, pro_doc)
	
	update_work_order_on_cancel(self,method)
	
	for item in self.items:
		if item.t_warehouse:
			item.batch_no = None
			item.db_set("batch_no",None)

	for data in frappe.get_all("Batch",{'reference_name': self.name, 'reference_doctype': self.doctype}):
		frappe.db.set_value('Batch',data.name,'reference_name','')
		frappe.db.set_value('Batch',data.name,'valuation_rate',0)
	batch_valuation_stock_entry_on_cancel(self,method)


def update_item_batches_based_on_fifo(self):
	if not self.get("get_batches_based_on_fifo"):
		return

	def get_item_details_custom(self, each_item):
		item_code = each_item.get("item_code") or each_item.get("item_name")
		item = frappe.db.sql(
			f"""
				SELECT 
					i.name, 
					i.stock_uom,
					i.description,
					i.image,
					i.item_name,
					i.item_group,
					i.has_batch_no,
					i.sample_quantity,
					i.has_serial_no,
					i.allow_alternative_item,
					id.expense_account,
					id.buying_cost_center
				FROM 
					`tabItem` AS i 
					LEFT JOIN `tabItem Default` AS id ON i.name = id.parent AND id.company = '{self.company}'
				WHERE
					i.name = '{item_code}'
					AND i.disabled=0
					AND (
						i.end_of_life is null 
						OR i.end_of_life='0000-00-00' OR 
						i.end_of_life > '{nowdate()}'
					)""",
			as_dict=1,
		)

		if not item:
			frappe.throw(
				_("Item {0} is not active or end of life has been reached").format(
					each_item.get("item_code")
				)
			)

		return item[0]

	new_items = []
	self.old_items = {}

	for row in self.items:
		item = get_item_details_custom(self, row)
		if row.get("s_warehouse", None) and row.get("qty") and item.get("has_batch_no"):
			if not self.old_items.get((row.item_code, row.s_warehouse)):
				self.old_items[(row.item_code, row.s_warehouse)] = []

			self.old_items[(row.item_code, row.s_warehouse)].append(row)
		else:
			new_items.append(row)

	self.items = []
	self.batches = []

	for item_warehouse_group in self.old_items.values():
		self.batches = []
		self.batches = get_fifo_batches(
			item_warehouse_group[0].item_code, item_warehouse_group[0].s_warehouse
		)
		for each_item in item_warehouse_group:
			item = get_item_details_custom(self, each_item)
			if (
				each_item.get("s_warehouse", None)
				and each_item.get("qty")
				and item.get("has_batch_no")
			):
				update_multiple_item_batch_based_on_fifo(self, each_item)

	self.get_batches_based_on_fifo = 0
	if new_items:
		self.items.extend(new_items)

	set_concentration_based_on_batch(self)


def stock_entry_calculate_rate_qty(self):
	for idx, item in enumerate(self.items):
		item.idx = idx + 1
	if (
		self.purpose in ["Material Receipt", "Repack"]
		and hasattr(self, "party_type")
		and hasattr(self, "reference_docname")
		and hasattr(self, "jw_ref")
	):
		if (
			not self.reference_docname
			and not self.jw_ref
			and self.party_type == "Supplier"
		):
			se_repack_cal_rate_qty(self)
		else:
			se_cal_rate_qty(self)
	else:
		se_cal_rate_qty(self)


def fg_completed_quantity_to_fg_completed_qty(self):
	if not frappe.db.get_value("Company", self.company, "maintain_as_is_new"):
		if self.fg_completed_qty == 0:
			self.fg_completed_qty = self.fg_completed_quantity


def get_fifo_batches(item_code, warehouse):
	batches = frappe.db.sql(
		f"""
		SELECT 
			batch_id,
			sum(actual_qty) as qty,
			concentration 
		FROM 
			`tabBatch` as b JOIN `tabStock Ledger Entry` AS sle ignore index (item_code, warehouse)
			on (b.batch_id = sle.batch_no)
		WHERE 
			sle.item_code = '{item_code}' 
			AND sle.warehouse = '{warehouse}'
			AND (b.expiry_date >= CURDATE() or `b`.expiry_date IS NULL)
		GROUP BY 
			sle.batch_no
		HAVING 
			sum(actual_qty) > 0 
		ORDER BY `b`.creation
	""",
		as_dict=True,
	)

	return batches


def update_multiple_item_batch_based_on_fifo(self, each_item):
	if not self.batches:
		return

	remaining_qty = flt(each_item.qty, 7)

	for idx, each_batch in enumerate(self.batches):
		if flt(each_batch.qty, 7) < 0.000001:
			continue

		if remaining_qty <= 0:
			break

		if idx == 0:
			use_qty = flt(
				each_batch.qty if remaining_qty > each_batch.qty else remaining_qty, 7
			)

			if not use_qty:
				continue

			self.add_to_stock_entry_detail(
				{
					each_item.item_code: {
						"from_warehouse": each_item.s_warehouse,
						"to_warehouse": each_item.t_warehouse,
						"qty": use_qty,
						"quantity": use_qty,
						"item_name": each_item.item_name,
						"item_code": each_item.item_code,
						"description": each_item.description,
						"stock_uom": each_item.stock_uom,
						"expense_account": each_item.expense_account,
						"cost_center": each_item.get("buying_cost_center"),
						"original_item": each_item.original_item,
						"batch_no": str(each_batch.batch_id),
						"item_group": each_item.get("item_group"),
						"is_process_loss": each_item.get("is_process_loss"),
						"is_scrap_item": each_item.get("is_scrap_item"),
						"is_finished_item": each_item.get("is_finished_item"),
						"concentration": flt(
							each_batch.concentration
							or each_item.get("concentration")
							or 100
						),
					}
				}
			)
			remaining_qty -= use_qty
			update_used_batches(self, str(each_batch.batch_id), use_qty)
		else:
			update_item = each_item.as_dict().copy()
			use_qty = flt(
				each_batch.qty if remaining_qty > each_batch.qty else remaining_qty, 7
			)
			update_item.batch_no = each_batch.batch_id
			update_item.qty = use_qty
			update_item.quantity = use_qty
			remaining_qty -= use_qty

			if not use_qty:
				continue

			self.add_to_stock_entry_detail(
				{
					each_item.item_code: {
						"from_warehouse": each_item.s_warehouse,
						"to_warehouse": each_item.t_warehouse,
						"qty": use_qty,
						"quantity": use_qty,
						"item_code": each_item.item_code,
						"item_name": each_item.item_name,
						"description": each_item.description,
						"stock_uom": each_item.stock_uom,
						"expense_account": each_item.expense_account,
						"cost_center": each_item.get("buying_cost_center"),
						"original_item": each_item.original_item,
						"batch_no": str(each_batch.batch_id),
						"item_group": each_item.get("item_group"),
						"is_process_loss": each_item.get("is_process_loss"),
						"is_scrap_item": each_item.get("is_scrap_item"),
						"is_finished_item": each_item.get("is_finished_item"),
						"concentration": flt(
							each_batch.concentration
							or each_item.get("concentration")
							or 100
						),
					}
				}
			)
			update_used_batches(self, str(each_batch.batch_id), use_qty)


def update_used_batches(self, batch_id, qty):
	for idx, each in enumerate(self.batches):
		if each.batch_id == batch_id:
			each.qty -= qty
			break


def set_concentration_based_on_batch(self):
	for each in self.items:
		if not each.get("concentration") and each.batch_no:
			each.concentration = flt(
				frappe.db.get_value("Batch", each.batch_no, "concentration")
			)
		elif not each.get("concentration"):
			each.concentration = flt(100)


def stock_entry_validate(self):
	if self.purpose == "Material Receipt":
		validate_batch_wise_item_for_concentration(self)


def validate_batch_wise_item_for_concentration(self):
	for row in self.items:
		has_batch_no = frappe.db.get_value("Item", row.item_code, "has_batch_no")

		if not has_batch_no:
			row.concentration = 100


def check_based_on_item(self):
	if self.purpose in ["Manufacture"]:
		item_list = [item.item_code for item in self.items]
		if self.based_on not in item_list:
			frappe.throw(
				"Based on Item {} Required in Raw Materials".format(
					frappe.bold(self.based_on)
				)
			)


def cal_validate_additional_cost_qty(self):
	if not frappe.db.get_value("Company", self.company, "maintain_as_is_new"):
		if self.additional_costs:
			for addi_cost in self.additional_costs:
				if addi_cost.qty and addi_cost.rate:
					addi_cost.amount = flt(addi_cost.qty) * flt(addi_cost.rate)
					addi_cost.base_amount = flt(addi_cost.qty) * flt(addi_cost.rate)
				if addi_cost.uom == "FG QTY":
					addi_cost.qty = self.fg_completed_quantity
					addi_cost.amount = flt(self.fg_completed_quantity) * flt(
						addi_cost.rate
					)
					addi_cost.base_amount = flt(self.fg_completed_quantity) * flt(
						addi_cost.rate
					)
	else:
		if self.additional_costs:
			for addi_cost in self.additional_costs:
				if addi_cost.qty and addi_cost.rate:
					addi_cost.amount = flt(addi_cost.qty) * flt(addi_cost.rate)
					addi_cost.base_amount = flt(addi_cost.qty) * flt(addi_cost.rate)
				if addi_cost.uom == "FG QTY":
					addi_cost.qty = self.fg_completed_qty
					addi_cost.amount = flt(self.fg_completed_qty) * flt(addi_cost.rate)
					addi_cost.base_amount = flt(self.fg_completed_qty) * flt(
						addi_cost.rate
					)


def calculate_rate_and_amount(
	self,
	reset_outgoing_rate=True,
	raise_error_if_no_rate=True,
):
	if self.purpose in ["Material Transfer for Manufacture"]:
		set_incoming_rate(self)
	if self.purpose in ["Manufacture", "Repack"]:
		is_multiple_finish = 0
		multi_item_list = []
		for d in self.items:
			if d.t_warehouse and d.qty != 0 and d.is_finished_item:
				is_multiple_finish += 1
				multi_item_list.append(d.item_code)
		if is_multiple_finish > 1 and self.purpose == "Manufacture":
			self.set_basic_rate(reset_outgoing_rate=False, raise_error_if_no_rate=True)
			bom_doc = frappe.get_doc("BOM", self.bom_no)
			if hasattr(bom_doc, "equal_cost_ratio"):
				if not bom_doc.equal_cost_ratio:
					cal_rate_for_finished_item(self)
				else:
					calculate_multiple_repack_valuation(self)
			else:
				cal_rate_for_finished_item(self)

		elif is_multiple_finish > 1 and self.purpose == "Repack":
			self.set_basic_rate(reset_outgoing_rate=False, raise_error_if_no_rate=True)
			calculate_multiple_repack_valuation(self)

		else:
			self.set_basic_rate(reset_outgoing_rate=True, raise_error_if_no_rate=True)
			self.distribute_additional_costs()

	else:
		self.set_basic_rate(reset_outgoing_rate=True, raise_error_if_no_rate=True)
		self.distribute_additional_costs()
	self.update_valuation_rate()
	# Finbyz Changes start: Calculate Valuation Price Based on Valuation Rate and concentration for AS IS Items
	update_valuation_price(self)
	# Finbyz Changes End
	self.set_total_incoming_outgoing_value()
	self.set_total_amount()
	price_to_rate(self)


def cal_rate_for_finished_item(self):
	if not frappe.db.get_value("Company", self.company, "maintain_as_is_new"):
		self.total_additional_costs = sum(
			[flt(t.amount) for t in self.get("additional_costs")]
		)
		work_order = frappe.get_doc("Work Order", self.work_order)
		work_order.ignore_permissions = True
		bom_doc = frappe.get_doc("BOM", self.bom_no)
		bom_doc.ignore_permissions = True
		is_multiple_finish = 0
		scrap_total = 0
		for d in self.items:
			if d.t_warehouse and not d.is_scrap_item:
				is_multiple_finish += 1
			if d.is_scrap_item:
				scrap_total += flt(d.basic_amount)
		if self.total_outgoing_value:
			self.total_outgoing_value = max(
				flt(self.total_outgoing_value) - flt(scrap_total), 0
			)
		if is_multiple_finish > 1:
			total_incoming_amount = 0.0
			item_arr = list()
			item_map = dict()
			finished_list = []
			result = {}
			cal_yield = 0
			if self.purpose == "Manufacture" and self.bom_no:
				for row in self.items:
					if row.t_warehouse and not d.is_scrap_item:
						finished_list.append(
							{row.item_code: row.quantity}
						)  # create a list of dict of finished item
				for d in finished_list:
					for k in d.keys():
						result[k] = (
							result.get(k, 0) + d[k]
						)  # create a dict of unique item

				for d in self.items:
					if d.item_code not in item_arr:
						item_map.setdefault(
							d.item_code, {"quantity": 0, "qty": 0, "yield_weights": 0}
						)

					item_map[d.item_code]["quantity"] += flt(d.quantity)
					item_map[d.item_code]["qty"] += flt(d.qty)
					item_map[d.item_code]["yield_weights"] += flt(d.batch_yield) * flt(
						d.quantity
					)

					bom_cost_list = []
					if bom_doc.is_multiple_item:
						for bom_fi in bom_doc.multiple_finish_item:
							bom_cost_list.append(
								{
									"item_code": bom_fi.item_code,
									"cost_ratio": bom_fi.cost_ratio,
								}
							)
					else:
						bom_cost_list.append(
							{"item_code": bom_doc.item, "cost_ratio": 100}
						)
					if d.t_warehouse:
						for bom_cost_dict in bom_cost_list:
							if d.item_code == bom_cost_dict["item_code"]:
								d.basic_amount = flt(
									flt(
										flt(self.total_outgoing_value)
										* flt(bom_cost_dict["cost_ratio"])
										* flt(d.quantity)
									)
									/ flt(100 * flt(result[d.item_code]))
								)
								d.additional_cost = flt(
									flt(
										flt(self.total_additional_costs)
										* flt(bom_cost_dict["cost_ratio"])
										* flt(d.quantity)
									)
									/ flt(100 * flt(result[d.item_code]))
								)
								d.amount = flt(d.basic_amount + d.additional_cost)
								d.basic_rate = flt(d.basic_amount / d.qty)
								d.valuation_rate = flt(d.amount / d.qty)

								item_yield = 0.0
								if item_map[self.based_on]["yield_weights"] > 0:
									item_yield = (
										item_map[self.based_on]["yield_weights"]
										/ item_map[self.based_on]["quantity"]
									)

								based_on_qty_ratio = d.quantity / (
									self.fg_completed_quantity or self.fg_completed_qty
								)
								if self.based_on:
									d.batch_yield = flt(
										(d.qty * d.concentration)
										/ (
											100
											* flt(
												item_map[self.based_on]["quantity"]
												* flt(based_on_qty_ratio)
												/ 100
											)
										)
									)
	else:
		self.total_additional_costs = sum(
			[flt(t.amount) for t in self.get("additional_costs")]
		)
		work_order = frappe.get_doc("Work Order", self.work_order)
		work_order.ignore_permissions = True
		bom_doc = frappe.get_doc("BOM", self.bom_no)
		bom_doc.ignore_permissions = True
		is_multiple_finish = 0
		scrap_total = 0
		for d in self.items:
			if d.t_warehouse and not d.is_scrap_item:
				is_multiple_finish += 1
			if d.is_scrap_item:
				scrap_total += flt(d.basic_amount)
		if self.total_outgoing_value:
			self.total_outgoing_value = max(
				flt(self.total_outgoing_value) - flt(scrap_total), 0
			)
		if is_multiple_finish > 1:
			total_incoming_amount = 0.0
			item_arr = list()
			item_map = dict()
			finished_list = []
			result = {}
			cal_yield = 0
			if self.purpose == "Manufacture" and self.bom_no:
				for row in self.items:
					if row.t_warehouse and not d.is_scrap_item:
						finished_list.append(
							{row.item_code: row.qty}
						)  # create a list of dict of finished item
				for d in finished_list:
					for k in d.keys():
						result[k] = (
							result.get(k, 0) + d[k]
						)  # create a dict of unique item

				for d in self.items:
					if d.item_code not in item_arr:
						item_map.setdefault(d.item_code, {"qty": 0, "yield_weights": 0})

					item_map[d.item_code]["qty"] += flt(d.qty)
					item_map[d.item_code]["yield_weights"] += flt(d.batch_yield) * flt(
						d.qty
					)

					bom_cost_list = []
					if bom_doc.is_multiple_item:
						for bom_fi in bom_doc.multiple_finish_item:
							bom_cost_list.append(
								{
									"item_code": bom_fi.item_code,
									"cost_ratio": bom_fi.cost_ratio,
								}
							)
					else:
						bom_cost_list.append(
							{"item_code": bom_doc.item, "cost_ratio": 100}
						)

					if d.t_warehouse:
						for bom_cost_dict in bom_cost_list:
							if d.item_code == bom_cost_dict["item_code"]:
								d.basic_amount = flt(
									flt(
										flt(self.total_outgoing_value)
										* flt(bom_cost_dict["cost_ratio"])
										* flt(d.qty)
									)
									/ flt(100 * flt(result[d.item_code]))
								)
								d.additional_cost = flt(
									flt(
										flt(self.total_additional_costs)
										* flt(bom_cost_dict["cost_ratio"])
										* flt(d.qty)
									)
									/ flt(100 * flt(result[d.item_code]))
								)
								d.amount = flt(d.basic_amount + d.additional_cost)
								d.basic_rate = flt(d.basic_amount / d.qty)
								d.valuation_rate = flt(d.amount / d.qty)

								item_yield = 0.0
								if item_map[self.based_on]["yield_weights"] > 0:
									item_yield = (
										item_map[self.based_on]["yield_weights"]
										/ item_map[self.based_on]["qty"]
									)

								based_on_qty_ratio = d.qty / (self.fg_completed_qty)
								if self.based_on:
									d.batch_yield = flt(
										(d.qty * d.concentration)
										/ (
											100
											* flt(
												item_map[self.based_on]["qty"]
												* flt(based_on_qty_ratio)
												/ 100
											)
										)
									)


def calculate_multiple_repack_valuation(self):
	self.total_additional_costs = sum(
		[flt(t.amount) for t in self.get("additional_costs")]
	)
	scrap_total = 0
	if self.items:
		qty = 0.0
		quantity = 0.0
		total_outgoing_value = 0.0
		for row in self.items:
			if row.s_warehouse:
				total_outgoing_value += flt(row.basic_amount)
			if row.t_warehouse:
				qty += row.qty
				if  not frappe.db.get_value("Company",self.company,"maintain_as_is_new"):
					quantity += row.quantity
			if row.is_scrap_item:
				scrap_total += flt(row.basic_amount)
		total_outgoing_value = flt(total_outgoing_value) - flt(scrap_total)
		for row in self.items:
			if row.t_warehouse:
				if  not frappe.db.get_value("Company",self.company,"maintain_as_is_new"):
					row.basic_amount = (
						flt(total_outgoing_value) * flt(row.quantity) / quantity
					)
					row.additional_cost = (
						flt(self.total_additional_costs) * flt(row.quantity) / quantity
					)
				row.basic_rate = flt(row.basic_amount / row.qty)


def update_valuation_price(self):
	if not frappe.db.get_value("Company", self.company, "maintain_as_is_new"):
		for item in self.items:
			maintain_as_is_stock = frappe.db.get_value(
				"Item", item.item_code, "maintain_as_is_stock"
			)
			concentration = item.concentration or 100
			if maintain_as_is_stock:
				item.valuation_price = (
					flt(item.valuation_rate) * 100 / flt(concentration)
				)
			else:
				item.valuation_price = flt(item.valuation_rate)
	else:
		pass


def price_to_rate(self):
	if not frappe.db.get_value("Company", self.company, "maintain_as_is_new"):
		for item in self.items:
			has_batch_no, maintain_as_is_stock = frappe.db.get_value(
				"Item", item.item_code, ["has_batch_no", "maintain_as_is_stock"]
			)
			concentration = item.concentration or 100
			if item.basic_rate:
				if maintain_as_is_stock:
					item.price = flt(item.basic_rate) * 100 / concentration
				else:
					item.price = flt(item.basic_rate)


def get_based_on(self):
	if self.work_order:
		self.based_on = frappe.db.get_value("Work Order", self.work_order, "based_on")


def cal_target_yield_cons(self):
	cal_yield = 0
	cons = 0
	tot_quan = 0
	item_arr = list()
	item_map = dict()
	flag = 0
	if self.purpose == "Manufacture" and self.based_on:
		if not frappe.db.get_value("Company", self.company, "maintain_as_is_new"):
			for d in self.items:
				if d.t_warehouse:
					flag += 1

				if d.item_code not in item_arr:
					item_map.setdefault(
						d.item_code, {"quantity": 0, "qty": 0, "yield_weights": 0}
					)

				item_map[d.item_code]["quantity"] += flt(d.quantity)
				item_map[d.item_code]["qty"] += flt(d.qty)
				item_map[d.item_code]["yield_weights"] += flt(d.batch_yield) * flt(
					d.quantity
				)

			if flag == 1:
				# Last row object
				last_row = self.items[-1]

				# Last row batch_yield
				batch_yield = last_row.batch_yield

				# List of item_code from items table
				items_list = [row.item_code for row in self.items]

				# Check if items list has frm.doc.based_on value
				if self.based_on in items_list:
					cal_yield = flt(last_row.qty / item_map[self.based_on]["quantity"])
				last_row.batch_yield = flt(cal_yield) * (
					flt(last_row.concentration) / 100.0
				)
		else:
			for d in self.items:
				if d.t_warehouse:
					flag += 1

				if d.item_code not in item_arr:
					item_map.setdefault(d.item_code, {"qty": 0, "yield_weights": 0})

				# item_map[d.item_code]['quantity'] += flt(d.quantity)
				item_map[d.item_code]["qty"] += flt(d.qty)
				item_map[d.item_code]["yield_weights"] += flt(d.batch_yield) * flt(
					d.qty
				)

			if flag == 1:
				# Last row object
				last_row = self.items[-1]

				# Last row batch_yield
				batch_yield = last_row.batch_yield

				# List of item_code from items table
				items_list = [row.item_code for row in self.items]

				# Check if items list has frm.doc.based_on value
				if self.based_on in items_list:
					cal_yield = flt(last_row.qty / item_map[self.based_on]["qty"])
				last_row.batch_yield = flt(cal_yield)


def validate_concentration(self):
	if self.work_order and self.purpose == "Manufacture":
		wo_item = frappe.db.get_value("Work Order", self.work_order, "production_item")
		for row in self.items:
			if row.t_warehouse and row.item_code == wo_item and not row.concentration:
				frappe.throw(
					_(
						"Add concentration in row {} for item {}".format(
							row.idx, row.item_code
						)
					)
				)


def validate_quality_inspection(self):
	if self.purpose == "Manufacture" and self.bom_no:
		if frappe.db.get_value("BOM", self.bom_no, "inspection_required") == 1:
			for row in self.items:
				if row.t_warehouse and not row.quality_inspection:
					frappe.throw(
						_(
							"Quality Inspection is mandatory in row {0} for item {1}.".format(
								row.idx, row.item_code
							)
						)
					)


def update_po(self):
	if self.work_order:
		po = frappe.get_doc("Work Order", self.work_order)
		if self.purpose == "Material Transfer for Manufacture":
			if po.material_transferred_for_manufacturing > po.qty:
				po.material_transferred_for_manufacturing = po.qty

			# if not frappe.db.exists({"doctype":"Stock Entry","name":("!=",self.name),"stock_entry_type":"Material Transfer for Manufacture","work_order":self.work_order}):
			if not frappe.db.sql(
				"""select name from `tabStock Entry` where name != '{}' and stock_entry_type = 'Material Transfer for Manufacture' and work_order = '{}'""".format(
					self.name, self.work_order
				)
			):
				po.db_set(
					"actual_start_date", self.posting_date + " " + self.posting_time
				)
		if not frappe.db.get_value("Company", self.company, "maintain_as_is_new"):
			if self.purpose == "Manufacture" and self.work_order:
				update_po_transfer_qty(self, po)
				update_po_items(self, po)

				# update finised item detail
				count = 0
				batch_yield = 0.0
				concentration = 0.0
				total_qty = 0.0
				actual_total_qty = 0.0
				valuation_rate = 0.0
				lot = []
				finished_item = {}
				finished_item_list = []
				s = ", "

				po.finish_item = []
				if po.bom_no:
					bom_doc = frappe.get_doc("BOM", po.bom_no)
				# po.append("finish_item",finished_item_list)
				finish_items_length = [
					item.idx for item in self.items if item.t_warehouse
				]
				for row in self.items:
					if row.t_warehouse and row.is_finished_item:
						count += 1
						batch_yield += row.batch_yield
						concentration += row.concentration
						total_qty += row.qty
						actual_total_qty += row.quantity
						valuation_rate += flt(row.qty) * flt(row.valuation_rate)
						if row.lot_no:
							lot.append(row.lot_no)
						if bom_doc.multiple_finish_item:
							for bom_fi in bom_doc.multiple_finish_item:
								if bom_fi.item_code == row.item_code:
									po.append(
										"finish_item",
										{
											"item_code": row.item_code,
											"actual_qty": row.qty,
											"actual_valuation": row.valuation_rate,
											"lot_no": row.lot_no,
											"purity": row.concentration,
											"packing_size": row.packing_size,
											"no_of_packages": row.no_of_packages,
											"batch_yield": row.batch_yield,
											"batch_no": row.batch_no,
											"bom_cost_ratio": bom_fi.cost_ratio,
											"bom_qty_ratio": bom_fi.qty_ratio,
											"bom_qty": po.qty * bom_fi.qty_ratio / 100,
											"bom_yield": bom_fi.batch_yield,
										},
									)
						else:
							po.append(
								"finish_item",
								{
									"item_code": row.item_code,
									"actual_qty": row.qty,
									"actual_valuation": row.valuation_rate,
									"lot_no": row.lot_no,
									"purity": row.concentration,
									"packing_size": row.packing_size,
									"no_of_packages": row.no_of_packages,
									"batch_yield": row.batch_yield,
									"batch_no": row.batch_no,
									"bom_cost_ratio": 100,
									"bom_qty_ratio": 100,
									"bom_qty": po.qty,
									"bom_yield": bom_doc.batch_yield,
								},
							)

				for child in po.finish_item:
					child.db_update()
				po.db_set("batch_yield", flt(batch_yield / count))
				po.db_set("concentration", flt(concentration / count))
				po.db_set("valuation_rate", valuation_rate / flt(actual_total_qty))
				po.db_set("produced_qty", total_qty)
				po.db_set("produced_quantity", actual_total_qty)
				# valuation price = valuation_rate * produced_qty / produced_quantity
				po.db_set(
					"valuation_price",
					(
						((flt(valuation_rate) / flt(actual_total_qty)) * total_qty)
						/ actual_total_qty
					),
				)
				if len(lot) != 0:
					po.db_set("lot_no", s.join(lot))
		else:
			if self.purpose == "Manufacture" and self.work_order:
				update_po_transfer_qty(self, po)
				update_po_items(self, po)

				# update finised item detail
				count = 0
				batch_yield = 0.0
				concentration = 0.0
				total_qty = 0.0
				actual_total_qty = 0.0
				valuation_rate = 0.0
				lot = []
				finished_item = {}
				finished_item_list = []
				s = ", "

				po.finish_item = []
				if po.bom_no:
					bom_doc = frappe.get_doc("BOM", po.bom_no)
				# po.append("finish_item",finished_item_list)
				finish_items_length = [
					item.idx for item in self.items if item.t_warehouse
				]
				for row in self.items:
					if row.t_warehouse and row.is_finished_item:
						count += 1
						batch_yield += row.batch_yield
						concentration += row.concentration
						total_qty += row.qty
						# actual_total_qty += row.quantity
						valuation_rate += flt(row.qty) * flt(row.valuation_rate)
						if row.lot_no:
							lot.append(row.lot_no)
						if bom_doc.multiple_finish_item:
							for bom_fi in bom_doc.multiple_finish_item:
								if bom_fi.item_code == row.item_code:
									po.append(
										"finish_item",
										{
											"item_code": row.item_code,
											"actual_qty": row.qty,
											"actual_valuation": row.valuation_rate,
											"lot_no": row.lot_no,
											"purity": row.concentration,
											"packing_size": row.packing_size,
											"no_of_packages": row.no_of_packages,
											"batch_yield": row.batch_yield,
											"batch_no": row.batch_no,
											"bom_cost_ratio": bom_fi.cost_ratio,
											"bom_qty_ratio": bom_fi.qty_ratio,
											"bom_qty": po.qty * bom_fi.qty_ratio / 100,
											"bom_yield": bom_fi.batch_yield,
										},
									)
						else:
							po.append(
								"finish_item",
								{
									"item_code": row.item_code,
									"actual_qty": row.qty,
									"actual_valuation": row.valuation_rate,
									"lot_no": row.lot_no,
									"purity": row.concentration,
									"packing_size": row.packing_size,
									"no_of_packages": row.no_of_packages,
									"batch_yield": row.batch_yield,
									"batch_no": row.batch_no,
									"bom_cost_ratio": 100,
									"bom_qty_ratio": 100,
									"bom_qty": po.qty,
									"bom_yield": bom_doc.batch_yield,
								},
							)

				for child in po.finish_item:
					child.db_update()
				po.db_set("batch_yield", flt(batch_yield / count))
				po.db_set("concentration", flt(concentration / count))
				po.db_set("valuation_rate", valuation_rate / flt(total_qty))
				po.db_set("produced_qty", total_qty)
				if len(lot) != 0:
					po.db_set("lot_no", s.join(lot))


def update_po_transfer_qty(self, po):
	if not frappe.db.get_value("Company", self.company, "maintain_as_is_new"):
		for d in po.required_items:
			se_items_date = frappe.db.sql(
				"""select sum(quantity), valuation_rate
				from `tabStock Entry` entry, `tabStock Entry Detail` detail
				where
					entry.work_order = %s
					and entry.purpose = "Material Transfer for Manufacture"
					and entry.docstatus = 1
					and detail.parent = entry.name
					and detail.s_warehouse is NOT NULL
					and detail.item_code = %s""",
				(po.name, d.item_code),
			)[0]

			d.db_set("transferred_qty", flt(se_items_date[0]), update_modified=False)
			d.db_set("valuation_rate", flt(se_items_date[1]), update_modified=False)
	else:
		for d in po.required_items:
			se_items_date = frappe.db.sql(
				"""select sum(qty), valuation_rate
				from `tabStock Entry` entry, `tabStock Entry Detail` detail
				where
					entry.work_order = %s
					and entry.purpose = "Material Transfer for Manufacture"
					and entry.docstatus = 1
					and detail.parent = entry.name
					and detail.s_warehouse is NOT NULL
					and detail.item_code = %s""",
				(po.name, d.item_code),
			)[0]

			d.db_set("transferred_qty", flt(se_items_date[0]), update_modified=False)
			d.db_set("valuation_rate", flt(se_items_date[1]), update_modified=False)


def update_po_items(self, po):
	from erpnext.stock.utils import get_latest_stock_qty
		
	if not frappe.db.get_value("Company", self.company, "maintain_as_is_new"):
		for row in self.items:
			if row.s_warehouse and not row.t_warehouse:
				item = [d.name for d in po.required_items if d.item_code == row.item_code]

				if not item:
					po.append(
						"required_items",
						{
							"item_code": row.item_code,
							"item_name": row.item_name,
							"description": row.description,
							"source_warehouse": row.s_warehouse,
							"required_qty": row.qty,
							"transferred_qty": row.quantity,
							"valuation_rate": row.valuation_rate,
							"available_qty_at_source_warehouse": get_latest_stock_qty(
								row.item_code, row.s_warehouse
							),
						},
					)

		for child in po.required_items:
			child.db_update()
	else:
		for row in self.items:
			if row.s_warehouse and not row.t_warehouse:
				item = [d.name for d in po.required_items if d.item_code == row.item_code]

				if not item:
					po.append(
						"required_items",
						{
							"item_code": row.item_code,
							"item_name": row.item_name,
							"description": row.description,
							"source_warehouse": row.s_warehouse,
							"required_qty": row.qty,
							"transferred_qty": row.qty,
							"valuation_rate": row.valuation_rate,
							"available_qty_at_source_warehouse": get_latest_stock_qty(
								row.item_code, row.s_warehouse
							),
						},
					)

		for child in po.required_items:
			child.db_update()



def set_batch_qc(self):
	for row in self.items:
		if row.quality_inspection:
			qi_doc = frappe.get_doc("Quality Inspection", row.quality_inspection)
			qi_doc.db_set("batch_no", row.batch_no, update_modified=False)


def delete_auto_created_batches(self):
	pass


def set_po_status(self, pro_doc):
	status = None
	if flt(pro_doc.material_transferred_for_instruction):
		status = "In Process"

	if status:
		pro_doc.db_set("status", status)


def update_po_volume(self, po, ignore_permissions=True):
	if self._action == "submit":
		po.save(ignore_permissions=True)

	elif self._action == "cancel":
		po.db_set("batch", "")
		po.save(ignore_permissions=True)


def update_work_order_on_cancel(self, method):
	if self.purpose == 'Manufacture' and self.work_order:
		frappe.db.sql("""delete from `tabWork Order Finish Item`
			where parent = %s""", self.work_order)