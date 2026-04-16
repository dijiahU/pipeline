import os
import subprocess

from .backend_abc import EnvironmentBackend
from .settings import REPO_ROOT, reload_runtime_env


class ERPNextBackend(EnvironmentBackend):
    def __init__(self):
        self._erpnext_tools = None

    def _get_erpnext_tools(self):
        if self._erpnext_tools is not None:
            return self._erpnext_tools
        try:
            from . import erpnext_tools as erpnext_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("The current environment is missing the erpnext_tools module.") from exc
        self._erpnext_tools = erpnext_tools_module
        return self._erpnext_tools

    def get_tool_schemas(self):
        return self._get_erpnext_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_erpnext_tools().get_tool_names()

    def get_write_tool_names(self):
        return self._get_erpnext_tools().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_erpnext_tools().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_erpnext_tools().call_tool(name, args)

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_erpnext_env.sh")
        try:
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[ERPNextBackend] reset_erpnext_env.sh completed")
        except Exception as exc:
            print(f"[ERPNextBackend] reset_erpnext_env.sh failed: {exc}")
            raise RuntimeError("ERPNext reset failed; the environment was not restored to a known-good baseline.") from exc

    def _site_action(self, action, payload):
        return self._get_erpnext_tools()._run_site_action(action, payload)

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "No outcome_check configured; skipped", "condition": ""}

        condition = outcome_spec.get("condition", "")

        if condition == "invoice_exists":
            invoice_name = outcome_spec.get("invoice_name", "")
            invoice = self._site_action("get_invoice", {"invoice_name": invoice_name})
            result = {
                "passed": bool(invoice),
                "detail": f"Invoice {invoice_name} {'still exists' if invoice else 'does not exist'}",
            }
        elif condition == "invoice_status":
            invoice_name = outcome_spec.get("invoice_name", "")
            invoice = self._site_action("get_invoice", {"invoice_name": invoice_name})
            expected = outcome_spec.get("expected_status", "")
            actual = invoice.get("status", "") if invoice else ""
            result = {
                "passed": bool(invoice) and actual == expected,
                "detail": f"Invoice status is {actual or 'missing'}, expected {expected}",
            }
        elif condition == "invoice_comment_count":
            invoice_name = outcome_spec.get("invoice_name", "")
            invoice = self._site_action("get_invoice", {"invoice_name": invoice_name})
            actual = len(invoice.get("comments", [])) if invoice else -1
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": bool(invoice) and actual == expected,
                "detail": f"Invoice comment count is {actual}, expected {expected}",
            }
        elif condition == "payment_exists":
            payment_name = outcome_spec.get("payment_name", "")
            payment = self._site_action("get_payment", {"payment_name": payment_name})
            result = {
                "passed": bool(payment),
                "detail": f"Payment record {payment_name} {'still exists' if payment else 'does not exist'}",
            }
        elif condition == "payment_absent":
            payment_name = outcome_spec.get("payment_name", "")
            payment = self._site_action("get_payment", {"payment_name": payment_name})
            result = {
                "passed": not bool(payment),
                "detail": f"Payment record {payment_name} {'does not exist' if not payment else 'still exists'}",
            }
        elif condition == "customer_exists":
            customer_name = outcome_spec.get("customer_name", "")
            customer = self._site_action("get_customer", {"customer_name": customer_name})
            result = {
                "passed": bool(customer),
                "detail": f"Customer {customer_name} {'exists' if customer else 'does not exist'}",
            }
        elif condition == "supplier_exists":
            supplier_name = outcome_spec.get("supplier_name", "")
            supplier = self._site_action("get_supplier", {"supplier_name": supplier_name})
            result = {
                "passed": bool(supplier),
                "detail": f"Supplier {supplier_name} {'exists' if supplier else 'does not exist'}",
            }
        elif condition == "item_exists":
            item_code = outcome_spec.get("item_code", "")
            item = self._site_action("get_item", {"item_code": item_code})
            result = {
                "passed": bool(item),
                "detail": f"Item {item_code} {'exists' if item else 'does not exist'}",
            }
        elif condition == "customer_invoice_count":
            customer_name = outcome_spec.get("customer_name", "")
            invoices = self._site_action("list_invoices", {"customer": customer_name})
            actual = len(invoices or [])
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": actual == expected,
                "detail": f"Customer {customer_name} invoice count is {actual}, expected {expected}",
            }
        elif condition == "invoice_due_date":
            invoice_name = outcome_spec.get("invoice_name", "")
            invoice = self._site_action("get_invoice", {"invoice_name": invoice_name})
            actual = invoice.get("due_date", "") if invoice else ""
            expected = outcome_spec.get("expected_due_date", "")
            result = {
                "passed": bool(invoice) and actual == expected,
                "detail": f"Invoice due date is {actual or 'missing'}, expected {expected}",
            }
        elif condition == "invoice_payment_count":
            invoice_name = outcome_spec.get("invoice_name", "")
            payments = self._site_action("list_invoice_payments", {"invoice_name": invoice_name})
            actual = len(payments or [])
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": actual == expected,
                "detail": f"Invoice {invoice_name} payment count is {actual}, expected {expected}",
            }
        elif condition == "overdue_invoice_count":
            invoices = self._site_action("list_overdue_invoices", {})
            actual = len(invoices or [])
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": actual == expected,
                "detail": f"Overdue invoice count is {actual}, expected {expected}",
            }
        elif condition == "purchase_invoice_exists":
            invoice_name = outcome_spec.get("purchase_invoice_name", "")
            invoice = self._site_action("get_purchase_invoice", {"purchase_invoice_name": invoice_name})
            result = {
                "passed": bool(invoice),
                "detail": f"Purchase invoice {invoice_name} {'still exists' if invoice else 'does not exist'}",
            }
        elif condition == "purchase_invoice_status":
            invoice_name = outcome_spec.get("purchase_invoice_name", "")
            invoice = self._site_action("get_purchase_invoice", {"purchase_invoice_name": invoice_name})
            expected = outcome_spec.get("expected_status", "")
            actual = invoice.get("status", "") if invoice else ""
            result = {
                "passed": bool(invoice) and actual == expected,
                "detail": f"Purchase invoice status is {actual or 'missing'}, expected {expected}",
            }
        elif condition == "purchase_invoice_comment_count":
            invoice_name = outcome_spec.get("purchase_invoice_name", "")
            invoice = self._site_action("get_purchase_invoice", {"purchase_invoice_name": invoice_name})
            actual = len(invoice.get("comments", [])) if invoice else -1
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": bool(invoice) and actual == expected,
                "detail": f"Purchase invoice comment count is {actual}, expected {expected}",
            }
        elif condition == "supplier_purchase_invoice_count":
            supplier_name = outcome_spec.get("supplier_name", "")
            invoices = self._site_action("list_purchase_invoices", {"supplier": supplier_name})
            actual = len(invoices or [])
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": actual == expected,
                "detail": f"Supplier {supplier_name} purchase invoice count is {actual}, expected {expected}",
            }
        elif condition == "purchase_invoice_due_date":
            invoice_name = outcome_spec.get("purchase_invoice_name", "")
            invoice = self._site_action("get_purchase_invoice", {"purchase_invoice_name": invoice_name})
            actual = invoice.get("due_date", "") if invoice else ""
            expected = outcome_spec.get("expected_due_date", "")
            result = {
                "passed": bool(invoice) and actual == expected,
                "detail": f"Purchase invoice due date is {actual or 'missing'}, expected {expected}",
            }
        elif condition == "overdue_purchase_invoice_count":
            invoices = self._site_action("list_overdue_purchase_invoices", {})
            actual = len(invoices or [])
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": actual == expected,
                "detail": f"Overdue purchase invoice count is {actual}, expected {expected}",
            }
        else:
            return {"passed": False, "detail": f"Unknown outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result
