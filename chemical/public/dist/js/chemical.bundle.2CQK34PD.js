(()=>{frappe.provide("chemical");chemical.CompanyAndItemDefaults=class extends frappe.ui.form.Controller{async setup(){await this.get_maintain_as_is_new()}async get_maintain_as_is_new(){this.frm.maintain_as_is_new=0,this.frm.doc.company&&await frappe.db.get_value("Company",this.frm.doc.company,"maintain_as_is_new").then(t=>{var l=t.message;this.frm.maintain_as_is_new=l.maintain_as_is_new})}};erpnext.TransactionController=class extends erpnext.TransactionController{make_quality_inspection(){console.log("make_quality_inspection");let t=[],l=[{label:"Items",fieldtype:"Table",fieldname:"items",cannot_add_rows:!0,in_place_edit:!0,data:t,get_data:()=>t,fields:[{fieldtype:"Data",fieldname:"docname",hidden:!0},{fieldtype:"Read Only",fieldname:"item_code",label:__("Item Code"),in_list_view:!0},{fieldtype:"Read Only",fieldname:"item_name",label:__("Item Name"),in_list_view:!0},{fieldtype:"Float",fieldname:"qty",label:__("Accepted Quantity"),in_list_view:!0,read_only:!0},{fieldtype:"Float",fieldname:"sample_size",label:__("Sample Size"),reqd:!0,in_list_view:!0},{fieldtype:"Data",fieldname:"description",label:__("Description"),hidden:!0},{fieldtype:"Data",fieldname:"serial_no",label:__("Serial No"),hidden:!0},{fieldtype:"Data",fieldname:"batch_no",label:__("Batch No"),hidden:!0},{fieldtype:"Data",fieldname:"ref_item",label:__("Ref Item"),hidden:!0},{fieldname:"concentration",fieldtype:"Percent",label:__("Concentration"),hidden:!0},{fieldname:"lot_no",fieldtype:"Data",label:__("Lot No"),hidden:!0}]}],n=this,i=new frappe.ui.Dialog({title:__("Select Items for Quality Inspection"),fields:l,primary_action:function(){let e=i.get_values();frappe.call({method:"erpnext.controllers.stock_controller.make_quality_inspections",args:{doctype:n.frm.doc.doctype,docname:n.frm.doc.name,items:e.items},freeze:!0,callback:function(a){a.message.length>0&&(a.message.length===1?frappe.set_route("Form","Quality Inspection",a.message[0]):(frappe.route_options={reference_type:n.frm.doc.doctype,reference_name:n.frm.doc.name},frappe.set_route("List","Quality Inspection"))),i.hide()}})},primary_action_label:__("Create")});this.frm.doc.items.forEach(e=>{if(this.has_inspection_required(e)){let a=i.fields_dict.items;a.df.data.push({docname:e.name,item_code:e.item_code,item_name:e.item_name,qty:e.qty,description:e.description,serial_no:e.serial_no,batch_no:e.batch_no,sample_size:e.sample_quantity,ref_item:e.name,concentration:e.concentration,lot_no:e.lot_no}),a.grid.refresh()}}),t=i.fields_dict.items.df.data,t.length?i.show():frappe.msgprint(__("All items in this document already have a linked Quality Inspection."))}};})();
//# sourceMappingURL=chemical.bundle.2CQK34PD.js.map
