#!/usr/bin/env python3
import json
import os
import base64
import sys
import traceback

import frappe
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete


def _parse_args():
    if len(sys.argv) < 3:
        raise SystemExit("usage: erpnext_site_ops.py <site> <action> [payload_json]")
    site = sys.argv[1]
    action = sys.argv[2]
    payload = {}
    payload_file = os.environ.get("PIPELINE_JSON_PAYLOAD_FILE", "")
    if payload_file and os.path.isfile(payload_file):
        with open(payload_file, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return site, action, payload
    payload_b64 = os.environ.get("PIPELINE_JSON_PAYLOAD_B64", "")
    if payload_b64:
        payload = json.loads(base64.b64decode(payload_b64).decode("utf-8"))
    elif len(sys.argv) > 3 and sys.argv[3]:
        payload = json.loads(sys.argv[3])
    return site, action, payload


def _json_print(payload):
    print(json.dumps(payload, ensure_ascii=False))


def _invoice_comment_count(invoice_name):
    return frappe.db.count(
        "Comment",
        {
            "reference_doctype": "Sales Invoice",
            "reference_name": invoice_name,
        },
    )


def _invoice_summary(doc):
    return {
        "name": doc.name,
        "customer": doc.customer,
        "status": doc.status,
        "outstanding_amount": float(doc.outstanding_amount or 0),
        "grand_total": float(doc.grand_total or 0),
        "comment_count": _invoice_comment_count(doc.name),
    }


def _payment_summary(doc):
    return {
        "name": doc.name,
        "party": doc.party,
        "status": doc.status,
        "paid_amount": float(doc.paid_amount or 0),
        "reference_no": doc.reference_no,
    }


def action_bootstrap(payload):
    if frappe.db.exists("Company", payload["company_name"]):
        return {"bootstrapped": False, "company_name": payload["company_name"]}

    args = {
        "language": payload.get("language", "English"),
        "country": payload.get("country", "United States"),
        "timezone": payload.get("timezone", "UTC"),
        "currency": payload.get("currency", "USD"),
        "full_name": payload.get("full_name", "Pipeline Admin"),
        "email": payload.get("email", "admin@example.com"),
        "password": payload.get("password", "admin"),
        "domain": payload.get("domain", "Services"),
        "company_name": payload["company_name"],
        "company_abbr": payload["company_abbr"],
        "company_tagline": payload.get("company_tagline", payload["company_name"]),
        "fy_start_date": payload.get("fy_start_date", "2026-01-01"),
        "fy_end_date": payload.get("fy_end_date", "2026-12-31"),
        "setup_website": 0,
        "add_sample_data": 0,
    }
    setup_complete(args)
    frappe.clear_cache()
    return {"bootstrapped": True, "company_name": payload["company_name"]}


def _ensure_customer(customer_name):
    if frappe.db.exists("Customer", customer_name):
        return frappe.get_doc("Customer", customer_name)
    doc = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Company",
            "customer_group": "All Customer Groups",
            "territory": "All Territories",
        }
    )
    doc.insert(ignore_permissions=True)
    return doc


def _ensure_item(item_code, item_name):
    if frappe.db.exists("Item", item_code):
        return frappe.get_doc("Item", item_code)
    doc = frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_name,
            "item_group": "All Item Groups",
            "stock_uom": "Nos",
            "is_stock_item": 0,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc


def _ensure_sales_invoice(entry, company_name):
    if frappe.db.exists("Sales Invoice", entry["name"]):
        doc = frappe.get_doc("Sales Invoice", entry["name"])
        if doc.docstatus == 0:
            doc.submit()
        return doc

    doc = frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "naming_series": "ACC-SINV-.YYYY.-.####",
            "customer": entry["customer"],
            "company": company_name,
            "posting_date": entry["posting_date"],
            "due_date": entry["due_date"],
            "set_posting_time": 1,
            "items": [
                {
                    "item_code": entry["item_code"],
                    "qty": 1,
                    "rate": float(entry["amount"]),
                }
            ],
        }
    )
    doc.insert(ignore_permissions=True)
    doc.submit()
    doc.reload()
    return doc


def _ensure_invoice_comment(invoice_name, comment, author):
    existing = frappe.get_all(
        "Comment",
        filters={
            "reference_doctype": "Sales Invoice",
            "reference_name": invoice_name,
            "content": comment,
        },
        fields=["name", "comment_email", "owner", "content"],
        limit=1,
    )
    if existing:
        return existing[0]
    doc = frappe.get_doc("Sales Invoice", invoice_name)
    comment_doc = doc.add_comment("Comment", comment)
    if author:
        comment_doc.comment_email = author
        comment_doc.save(ignore_permissions=True)
    return {
        "name": comment_doc.name,
        "comment_email": comment_doc.comment_email,
        "content": comment_doc.content,
    }


def _ensure_payment_entry(entry):
    if frappe.db.exists("Payment Entry", entry["name"]):
        return frappe.get_doc("Payment Entry", entry["name"])

    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    payment_doc = get_payment_entry("Sales Invoice", entry["invoice_name"])
    payment_doc.naming_series = "ACC-PAY-.YYYY.-.####"
    payment_doc.reference_no = entry.get("reference_no") or entry["name"]
    payment_doc.reference_date = entry.get("reference_date") or entry["posting_date"]
    payment_doc.posting_date = entry["posting_date"]
    payment_doc.paid_amount = float(entry["amount"])
    payment_doc.received_amount = float(entry["amount"])
    payment_doc.insert(ignore_permissions=True)
    payment_doc.reload()
    return payment_doc


def action_seed(payload):
    company_name = payload["bootstrap"]["company_name"]
    for customer_name in payload.get("customers", []):
        _ensure_customer(customer_name)

    for item in payload.get("items", []):
        _ensure_item(item["item_code"], item["item_name"])

    invoices = []
    for invoice_entry in payload.get("invoices", []):
        _ensure_customer(invoice_entry["customer"])
        _ensure_item(invoice_entry["item_code"], invoice_entry["item_name"])
        invoice_doc = _ensure_sales_invoice(invoice_entry, company_name)
        invoices.append(_invoice_summary(invoice_doc))
        for comment_entry in invoice_entry.get("comments", []):
            _ensure_invoice_comment(invoice_doc.name, comment_entry["content"], comment_entry.get("author", ""))

    payments = []
    for payment_entry in payload.get("payments", []):
        payment_doc = _ensure_payment_entry(payment_entry)
        payments.append(_payment_summary(payment_doc))

    return {"invoices": invoices, "payments": payments}


def action_list_invoices(payload):
    filters = {}
    if payload.get("status"):
        filters["status"] = payload["status"]
    if payload.get("customer"):
        filters["customer"] = payload["customer"]
    docs = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=["name"],
        order_by="name asc",
    )
    return [_invoice_summary(frappe.get_doc("Sales Invoice", row["name"])) for row in docs]


def action_get_invoice(payload):
    name = payload["invoice_name"]
    if not frappe.db.exists("Sales Invoice", name):
        return None
    doc = frappe.get_doc("Sales Invoice", name)
    comments = frappe.get_all(
        "Comment",
        filters={
            "reference_doctype": "Sales Invoice",
            "reference_name": name,
        },
        fields=["name", "comment_email", "content", "creation"],
        order_by="creation asc",
    )
    data = _invoice_summary(doc)
    data["comments"] = [
        {
            "id": item["name"],
            "author": item.get("comment_email") or "",
            "comment": item.get("content") or "",
            "creation": str(item.get("creation") or ""),
        }
        for item in comments
    ]
    return data


def action_list_payments(payload):
    filters = {}
    if payload.get("status"):
        filters["status"] = payload["status"]
    docs = frappe.get_all(
        "Payment Entry",
        filters=filters,
        fields=["name"],
        order_by="name asc",
    )
    return [_payment_summary(frappe.get_doc("Payment Entry", row["name"])) for row in docs]


def action_get_payment(payload):
    name = payload["payment_name"]
    if not frappe.db.exists("Payment Entry", name):
        return None
    return _payment_summary(frappe.get_doc("Payment Entry", name))


def action_add_invoice_comment(payload):
    invoice_name = payload["invoice_name"]
    if not frappe.db.exists("Sales Invoice", invoice_name):
        raise frappe.DoesNotExistError(f"Sales Invoice {invoice_name} does not exist")
    comment_doc = _ensure_invoice_comment(invoice_name, payload["comment"], payload.get("author", ""))
    return {
        "invoice_name": invoice_name,
        "comment_id": comment_doc["name"],
        "author": comment_doc.get("comment_email") or payload.get("author", ""),
        "comment": comment_doc["content"],
    }


def action_update_invoice_status(payload):
    invoice_name = payload["invoice_name"]
    status = payload["status"]
    if status.lower() != "paid":
        raise ValueError(f"unsupported invoice status transition: {status}")
    if not frappe.db.exists("Sales Invoice", invoice_name):
        raise frappe.DoesNotExistError(f"Sales Invoice {invoice_name} does not exist")
    invoice = frappe.get_doc("Sales Invoice", invoice_name)
    if invoice.status == "Paid":
        payment_name = frappe.db.get_value(
            "Payment Entry Reference",
            {"reference_doctype": "Sales Invoice", "reference_name": invoice_name},
            "parent",
        )
        return {"invoice_name": invoice_name, "status": invoice.status, "payment_name": payment_name}

    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    payment_doc = get_payment_entry("Sales Invoice", invoice_name)
    payment_doc.naming_series = "ACC-PAY-.YYYY.-.####"
    payment_doc.reference_no = f"PIPELINE-{invoice_name}"
    payment_doc.reference_date = frappe.utils.nowdate()
    payment_doc.posting_date = frappe.utils.nowdate()
    payment_doc.paid_amount = float(invoice.outstanding_amount or payment_doc.paid_amount or 0)
    payment_doc.received_amount = float(invoice.outstanding_amount or payment_doc.received_amount or 0)
    payment_doc.insert(ignore_permissions=True)
    payment_doc.submit()
    invoice.reload()
    return {"invoice_name": invoice_name, "status": invoice.status, "payment_name": payment_doc.name}


def action_delete_payment_entry(payload):
    payment_name = payload["payment_name"]
    if not frappe.db.exists("Payment Entry", payment_name):
        raise frappe.DoesNotExistError(f"Payment Entry {payment_name} does not exist")
    payment_doc = frappe.get_doc("Payment Entry", payment_name)
    if payment_doc.docstatus == 1:
        payment_doc.cancel()
    frappe.delete_doc("Payment Entry", payment_name, ignore_permissions=True, force=1)
    return {"deleted_payment_name": payment_name}


ACTIONS = {
    "bootstrap": action_bootstrap,
    "seed": action_seed,
    "list_invoices": action_list_invoices,
    "get_invoice": action_get_invoice,
    "list_payments": action_list_payments,
    "get_payment": action_get_payment,
    "add_invoice_comment": action_add_invoice_comment,
    "update_invoice_status": action_update_invoice_status,
    "delete_payment_entry": action_delete_payment_entry,
}


def main():
    site, action, payload = _parse_args()
    handler = ACTIONS.get(action)
    if handler is None:
        raise SystemExit(f"unknown action: {action}")

    os.makedirs("/home/frappe/logs", exist_ok=True)
    bench_site_path = f"/home/frappe/frappe-bench/{site}"
    actual_site_path = f"/home/frappe/frappe-bench/sites/{site}"
    if not os.path.exists(bench_site_path) and os.path.exists(actual_site_path):
        os.symlink(actual_site_path, bench_site_path)
    frappe.init(site=site, sites_path="sites")
    frappe.connect()
    try:
        result = handler(payload)
        frappe.db.commit()
        _json_print(result)
    except Exception:
        frappe.db.rollback()
        traceback.print_exc(file=sys.stderr)
        raise
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
