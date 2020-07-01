def correcting_valuation():
	#doc=frappe.get_doc("Correcting Valuation", 'CV004')
	doc=frappe.new_doc('Correcting Valuation')

	sales_invoice_list = frappe.db.sql("""
		select name 
		from `tabSales Invoice`
		where  docstatus=1 and update_stock=1 
		order by concat_ws(" ", posting_date, posting_time) desc
		""",as_dict=True)
	print("Sales Invoice Cancel", len(sales_invoice_list))
	for idx, x in enumerate(sales_invoice_list):
		si = frappe.get_doc("Sales Invoice",x['name'])
		si.flags.ignore_links = True
		modified_date = si.modified
		try:
			si.cancel()
			doc.append('cancelled_invoice',{
				'cancelled_invoice': x['name'],
				'modified_date': modified_date
			})
		except Exception as e:
			print(idx, "Exception:", si.name, e)
			doc.append('cancelled_error',{
				'cancelled_error': x['name'],
				'error': str(e),
				'reference_doctype': "Stock Entry",
				'modified_date': modified_date
			})
		else:
			print(idx, "Success:", si.name)
		if idx % 30 == 0:
			doc.save()	
	doc.save()

	stock_entry_out_list = frappe.db.sql("""
		select name 
		from `tabStock Entry` 
		where docstatus=1 and company = 'Meera Dyestuff Industries' and is_opening = 'No'
		order by is_opening ASC, concat_ws(" ", posting_date, posting_time) desc
		""",as_dict=True)   
	# se_out_list = []
	print("Stock Entry Cancel:", len(stock_entry_out_list))
	for idx, x in enumerate(stock_entry_out_list):
		se = frappe.get_doc("Stock Entry",x['name'])
		modified_date = se.modified
		try:
			se.flags.ignore_links = True
			se.cancel()
			doc.append('cancelled_stock_out_entry',{
				'cancelled_stock_out_entry': x['name'],
				'modified_date': modified_date
			})
			# se_out_list.append(x['name'])
		except Exception as e:
			print(idx, "Exception:", se.name, e)
			doc.append('cancelled_error',{
				'cancelled_error': x['name'],
				'error': str(e),
				'reference_doctype': "Stock Entry",
				'modified_date': modified_date
			})
		else:
			print(idx, "Success:", se.name)
		if idx % 30 == 0:
			doc.save()
	doc.save()

	data = frappe.get_all("Backup SE Batch", order_by = 'modified', fields = ['old_batch', 'new_batch'])[::-1]

	for x in data:
		frappe.db.sql(f"UPDATE `tabStock Entry Detail` SET batch_no = '{x.old_batch}' WHERE batch_no = '{x.new_batch}' AND docstatus = 2")

	for x in data:
		frappe.db.sql(f"UPDATE `tabSales Invoice Item` SET batch_no = '{x.old_batch}' WHERE batch_no = '{x.new_batch}' AND docstatus = 2")

		purchase_invoice_list = frappe.db.sql("""
			select name 
			from `tabPurchase Invoice`
			where  docstatus=1 and update_stock=1 
			order by concat_ws(" ", posting_date, posting_time) desc
			""",as_dict=True)
		pi_list = []
		print("Purchase Invoice Cancel:", len(purchase_invoice_list))
		for idx, x in enumerate(purchase_invoice_list):
			pi = frappe.get_doc("Purchase Invoice",x['name'])
			modified_date = pi.modified
			try:
				pi.cancel()
				doc.append('cancelled_purchase_invoice',{
					'cancelled_purchase_invoice': x['name'],
					'modified_date': modified_date
				})
				pi_list.append(x['name'])
			except Exception as e:
				print(idx, "Exception:", pi.name)
				doc.append('cancelled_error',{
					'cancelled_error': x['name'],
					'error': str(e),
					'reference_doctype': "Purchase Invoice",
					'modified_date': modified_date
				})
			else:
				print(idx, "Success:", pi.name)
			if idx % 30 == 0:
				doc.save()
		doc.save()

	purchase_receipt_list = frappe.db.sql("""
		select name 
		from `tabPurchase Receipt`
		where  docstatus=1
		order by concat_ws(" ", posting_date, posting_time) desc
		""",as_dict=True)
	print("Purchase Receipt Cancel:", len(purchase_receipt_list))
	for idx, x in enumerate(purchase_receipt_list):
		pr = frappe.get_doc("Purchase Receipt",x['name'])
		modified_date = pr.modified
		pr.flags.ignore_links = True
		try:
			pr.cancel()
			doc.append('cancelled_purchase_receipt',{
				'cancelled_purchase_receipt': x['name'],
				'modified_date': modified_date
			})
		except Exception as e:
			print(idx, "Exception:", pr.name, e)
			doc.append('cancelled_error',{
				'cancelled_error': x['name'],
				'error': str(e),
				'reference_doctype': "Purchase Receipt",
				'modified_date': modified_date
			})
		else:
			print(idx, "Success:", pr.name)
		
		if idx % 30 == 0:
			doc.save()

	doc.save()
	frappe.db.sql("SET SQL_SAFE_UPDATES = 0")


	print("Submiting Purchase Receipt")
	for item in doc.cancelled_purchase_receipt[::-1]:
		pr_doc = frappe.get_doc("Purchase Receipt", item.cancelled_purchase_receipt)
		print(pr_doc.name)
		frappe.db.sql(f"UPDATE `tabPurchase Receipt Item` SET docstatus = 1 WHERE parent = '{pr_doc.name}'")
		pr_doc.db_set('docstatus', 0)
		pr_doc.db_set('set_posting_time', 1)
		pr_doc.save()
		pr_doc.submit()
		pr_doc.db_set('modified', item.modified_date)

	print("Submiting Purchase Invoice")
	for idx, item in enumerate(doc.cancelled_purchase_invoice[::-1]):
		if idx >= 0:
			pi_doc = frappe.get_doc("Purchase Invoice", item.cancelled_purchase_invoice)
			print(idx, pi_doc.name)
			frappe.db.sql(f"UPDATE `tabPurchase Invoice Item` SET docstatus = 1 WHERE parent = '{pi_doc.name}'")
			pi_doc.db_set('docstatus', 0)
			pi_doc.db_set('set_posting_time', 1)
			pi_doc.save()
			pi_doc.submit()
			pi_doc.db_set('modified', item.modified_date)
			
	print("Submiting Stock Entry")
	for idx, item in enumerate(doc.cancelled_stock_out_entry[::-1]):
		if idx >= 0:
			se_doc = frappe.get_doc("Stock Entry", item.cancelled_stock_out_entry)
			print(idx, se_doc.name)
			frappe.db.sql(f"UPDATE `tabStock Entry Detail` SET docstatus = 1 WHERE parent = '{se_doc.name}'")
			se_doc.db_set('docstatus', 0)
			se_doc.db_set('set_posting_time', 1)
			se_doc.save()
			se_doc.submit()
			se_doc.db_set('modified', item.modified_date)

	print("Submiting Sales Invoice")
	for idx, item in enumerate(doc.cancelled_invoice[::-1]):
		if idx >= 0:
			si_doc = frappe.get_doc("Sales Invoice", item.cancelled_invoice)
			print(idx, si_doc.name)
			frappe.db.sql(f"UPDATE `tabSales Invoice Item` SET docstatus = 1 WHERE parent = '{si_doc.name}'")
			si_doc.db_set('docstatus', 0)
			si_doc.db_set('set_posting_time', 1)
			si_doc.save()
			si_doc.submit()
			si_doc.db_set('modified', item.modified_date)
	
	frappe.db.sql("SET SQL_SAFE_UPDATES=0")
	frappe.db.sql("DELETE FROM `tabVersion` WHERE owner='Administrator' and modified > '2020-06-24 00:00:00'")
