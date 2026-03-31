"""
ERPNext 真实站点工具。
"""

import base64
import json
import os
import subprocess

from .exceptions import ToolExecutionError
from .settings import REPO_ROOT
from .service_tools import ServiceToolRegistry


_REGISTRY = ServiceToolRegistry(service_id="erpnext")


def erpnext_tool(name, description, params, required=None, is_write=False, group="", short_description=""):
    return _REGISTRY.register(
        name=name,
        description=description,
        params=params,
        required=required,
        is_write=is_write,
        group=group,
        short_description=short_description,
    )


def get_all_schemas():
    return _REGISTRY.get_all_schemas()


def call_tool(name, args):
    return _REGISTRY.call_tool(name, args)


def get_tool_names():
    return _REGISTRY.get_tool_names()


def get_write_tool_names():
    return _REGISTRY.get_write_tool_names()


def get_tool_summary():
    return _REGISTRY.get_tool_summary()


def _backend_container():
    return os.environ.get("ERPNEXT_BACKEND_CONTAINER", "pipeline-erpnext-backend")


def _site_name():
    return os.environ.get("ERPNEXT_SITE_NAME", "frontend")


def _site_ops_script():
    return "/opt/pipeline/scripts/erpnext_site_ops.py"


def _run_site_action(action, payload=None):
    payload = payload or {}
    payload_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    cmd = [
        "docker",
        "exec",
        "-e",
        f"PIPELINE_JSON_PAYLOAD_B64={payload_b64}",
        _backend_container(),
        "bash",
        "-lc",
        (
            "cd /home/frappe/frappe-bench && "
            f"/home/frappe/frappe-bench/env/bin/python {_site_ops_script()} {_site_name()} {action} "
        ),
    ]
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise ToolExecutionError(f"[ERPNext 操作失败] {detail}")
    try:
        return json.loads(result.stdout.strip() or "null")
    except json.JSONDecodeError as exc:
        raise ToolExecutionError(f"[ERPNext 返回了非 JSON 输出] {result.stdout[:500]}") from exc


def _format_json(data):
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=2)


@erpnext_tool(
    "list_invoices",
    "列出销售发票，可按状态或客户筛选。",
    {
        "status": {"type": "string", "description": "状态，如 Unpaid、Paid、Overdue"},
        "customer": {"type": "string", "description": "客户名称"},
    },
    group="invoices",
    short_description="List sales invoices with optional status or customer filters.",
)
def list_invoices(status="", customer=""):
    return _format_json(_run_site_action("list_invoices", {"status": status, "customer": customer}))


@erpnext_tool(
    "list_customer_invoices",
    "列出某个客户的发票，可按状态筛选。",
    {
        "customer_name": {"type": "string", "description": "客户名称"},
        "status": {"type": "string", "description": "状态，如 Unpaid、Paid、Overdue"},
    },
    required=["customer_name"],
    group="invoices",
    short_description="List invoices for one customer with optional status filtering.",
)
def list_customer_invoices(customer_name, status=""):
    return _format_json(
        _run_site_action(
            "list_customer_invoices",
            {"customer_name": customer_name, "status": status},
        )
    )


@erpnext_tool(
    "list_overdue_invoices",
    "列出当前已逾期且仍有未结清金额的发票。",
    {},
    group="invoices",
    short_description="List overdue invoices that still have outstanding balance.",
)
def list_overdue_invoices():
    return _format_json(_run_site_action("list_overdue_invoices"))


@erpnext_tool(
    "get_invoice",
    "获取单个销售发票详情，包括明细行、备注和关联付款。",
    {
        "invoice_name": {"type": "string", "description": "发票编号"},
    },
    required=["invoice_name"],
    group="invoices",
    short_description="Read one invoice with items, comments, and linked payments.",
)
def get_invoice(invoice_name):
    payload = _run_site_action("get_invoice", {"invoice_name": invoice_name})
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到发票: {invoice_name}")
    return _format_json(payload)


@erpnext_tool(
    "list_invoice_comments",
    "列出某张发票的备注历史。",
    {
        "invoice_name": {"type": "string", "description": "发票编号"},
    },
    required=["invoice_name"],
    group="invoice_comments",
    short_description="List comment history attached to one invoice.",
)
def list_invoice_comments(invoice_name):
    return _format_json(_run_site_action("list_invoice_comments", {"invoice_name": invoice_name}))


@erpnext_tool(
    "add_invoice_comment",
    "为发票追加一条备注。",
    {
        "invoice_name": {"type": "string", "description": "发票编号"},
        "comment": {"type": "string", "description": "备注内容"},
        "author": {"type": "string", "description": "备注作者"},
    },
    required=["invoice_name", "comment"],
    is_write=True,
    group="invoice_comments",
    short_description="Append a finance note to an existing invoice.",
)
def add_invoice_comment(invoice_name, comment, author="accounts-bot"):
    return _format_json(
        _run_site_action(
            "add_invoice_comment",
            {
                "invoice_name": invoice_name,
                "comment": comment,
                "author": author,
            },
        )
    )


@erpnext_tool(
    "update_invoice_due_date",
    "更新销售发票的到期日。",
    {
        "invoice_name": {"type": "string", "description": "发票编号"},
        "due_date": {"type": "string", "description": "新的到期日，如 2026-04-30"},
    },
    required=["invoice_name", "due_date"],
    is_write=True,
    group="invoice_updates",
    short_description="Change the due date of one existing invoice.",
)
def update_invoice_due_date(invoice_name, due_date):
    return _format_json(
        _run_site_action(
            "update_invoice_due_date",
            {"invoice_name": invoice_name, "due_date": due_date},
        )
    )


@erpnext_tool(
    "update_invoice_status",
    "更新发票状态。当前支持把销售发票标记为 Paid。",
    {
        "invoice_name": {"type": "string", "description": "发票编号"},
        "status": {"type": "string", "description": "新状态，如 Paid"},
    },
    required=["invoice_name", "status"],
    is_write=True,
    group="invoice_updates",
    short_description="Mark one invoice as paid by creating the matching payment entry.",
)
def update_invoice_status(invoice_name, status):
    return _format_json(
        _run_site_action(
            "update_invoice_status",
            {"invoice_name": invoice_name, "status": status},
        )
    )


@erpnext_tool(
    "cancel_invoice",
    "取消销售发票。已提交的发票才能取消。",
    {
        "invoice_name": {"type": "string", "description": "发票编号"},
    },
    required=["invoice_name"],
    is_write=True,
    group="invoice_updates",
    short_description="Cancel a submitted invoice.",
)
def cancel_invoice(invoice_name):
    return _format_json(_run_site_action("cancel_invoice", {"invoice_name": invoice_name}))


@erpnext_tool(
    "create_invoice",
    "创建销售发票。",
    {
        "customer": {"type": "string", "description": "客户名称"},
        "items": {
            "type": "array",
            "items": {"type": "object"},
            "description": "发票行项目列表，每项包含 item_code、qty、rate，可选 item_name",
        },
        "due_date": {"type": "string", "description": "到期日期，如 2026-04-15"},
    },
    required=["customer", "items"],
    is_write=True,
    group="invoices",
    short_description="Create a submitted sales invoice for a customer.",
)
def create_invoice(customer, items, due_date=""):
    return _format_json(
        _run_site_action(
            "create_invoice",
            {
                "customer": customer,
                "items": items,
                "due_date": due_date,
            },
        )
    )


@erpnext_tool(
    "list_payments",
    "列出付款记录，可按状态或客户筛选。",
    {
        "status": {"type": "string", "description": "状态，如 Draft、Submitted"},
        "party": {"type": "string", "description": "客户名称"},
    },
    group="payments",
    short_description="List payment entries with optional status or customer filter.",
)
def list_payments(status="", party=""):
    return _format_json(_run_site_action("list_payments", {"status": status, "party": party}))


@erpnext_tool(
    "list_invoice_payments",
    "列出某张发票关联的付款记录。",
    {
        "invoice_name": {"type": "string", "description": "发票编号"},
    },
    required=["invoice_name"],
    group="payments",
    short_description="List payment entries linked to one invoice.",
)
def list_invoice_payments(invoice_name):
    return _format_json(_run_site_action("list_invoice_payments", {"invoice_name": invoice_name}))


@erpnext_tool(
    "get_payment",
    "获取单个付款记录详情。",
    {
        "payment_name": {"type": "string", "description": "付款记录编号"},
    },
    required=["payment_name"],
    group="payments",
    short_description="Read one payment entry including linked references.",
)
def get_payment(payment_name):
    payload = _run_site_action("get_payment", {"payment_name": payment_name})
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到付款记录: {payment_name}")
    return _format_json(payload)


@erpnext_tool(
    "create_payment_entry",
    "创建付款记录。",
    {
        "invoice_name": {"type": "string", "description": "关联的发票编号"},
        "amount": {"type": "number", "description": "付款金额"},
        "mode_of_payment": {"type": "string", "description": "付款方式，如 Cash、Wire Transfer"},
    },
    required=["invoice_name", "amount"],
    is_write=True,
    group="payments",
    short_description="Create a payment entry against one invoice.",
)
def create_payment_entry(invoice_name, amount, mode_of_payment="Cash"):
    return _format_json(
        _run_site_action(
            "create_payment_entry",
            {
                "invoice_name": invoice_name,
                "amount": amount,
                "mode_of_payment": mode_of_payment,
            },
        )
    )


@erpnext_tool(
    "delete_payment_entry",
    "删除付款记录。",
    {
        "payment_name": {"type": "string", "description": "付款记录编号"},
    },
    required=["payment_name"],
    is_write=True,
    group="payments",
    short_description="Delete a payment entry, cancelling it first if needed.",
)
def delete_payment_entry(payment_name):
    return _format_json(_run_site_action("delete_payment_entry", {"payment_name": payment_name}))


@erpnext_tool(
    "list_customers",
    "列出客户，可按名称筛选。",
    {
        "name_query": {"type": "string", "description": "客户名称关键词"},
    },
    group="customers",
    short_description="List customer master records with optional name filtering.",
)
def list_customers(name_query=""):
    return _format_json(_run_site_action("list_customers", {"name_query": name_query}))


@erpnext_tool(
    "get_customer",
    "获取单个客户详情，包括其关联发票。",
    {
        "customer_name": {"type": "string", "description": "客户名称"},
    },
    required=["customer_name"],
    group="customers",
    short_description="Read one customer profile with linked invoices.",
)
def get_customer(customer_name):
    payload = _run_site_action("get_customer", {"customer_name": customer_name})
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到客户: {customer_name}")
    return _format_json(payload)


@erpnext_tool(
    "create_customer",
    "创建客户主数据。",
    {
        "customer_name": {"type": "string", "description": "客户名称"},
        "customer_type": {"type": "string", "description": "客户类型，如 Company、Individual"},
        "customer_group": {"type": "string", "description": "客户组，默认 All Customer Groups"},
        "territory": {"type": "string", "description": "Territory，默认 All Territories"},
    },
    required=["customer_name"],
    is_write=True,
    group="customers",
    short_description="Create a new customer master record.",
)
def create_customer(customer_name, customer_type="Company", customer_group="All Customer Groups", territory="All Territories"):
    return _format_json(
        _run_site_action(
            "create_customer",
            {
                "customer_name": customer_name,
                "customer_type": customer_type,
                "customer_group": customer_group,
                "territory": territory,
            },
        )
    )


@erpnext_tool(
    "list_suppliers",
    "列出供应商，可按名称筛选。",
    {
        "name_query": {"type": "string", "description": "供应商名称关键词"},
    },
    group="suppliers",
    short_description="List supplier master records with optional name filtering.",
)
def list_suppliers(name_query=""):
    return _format_json(_run_site_action("list_suppliers", {"name_query": name_query}))


@erpnext_tool(
    "get_supplier",
    "获取单个供应商详情，包括其关联采购发票。",
    {
        "supplier_name": {"type": "string", "description": "供应商名称"},
    },
    required=["supplier_name"],
    group="suppliers",
    short_description="Read one supplier profile with linked purchase invoices.",
)
def get_supplier(supplier_name):
    payload = _run_site_action("get_supplier", {"supplier_name": supplier_name})
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到供应商: {supplier_name}")
    return _format_json(payload)


@erpnext_tool(
    "create_supplier",
    "创建供应商主数据。",
    {
        "supplier_name": {"type": "string", "description": "供应商名称"},
        "supplier_type": {"type": "string", "description": "供应商类型，如 Company、Individual"},
        "supplier_group": {"type": "string", "description": "供应商组，默认 All Supplier Groups"},
    },
    required=["supplier_name"],
    is_write=True,
    group="suppliers",
    short_description="Create a new supplier master record.",
)
def create_supplier(supplier_name, supplier_type="Company", supplier_group="All Supplier Groups"):
    return _format_json(
        _run_site_action(
            "create_supplier",
            {
                "supplier_name": supplier_name,
                "supplier_type": supplier_type,
                "supplier_group": supplier_group,
            },
        )
    )


@erpnext_tool(
    "list_items",
    "列出 Item 目录，可按 item code 或 item name 关键词筛选。",
    {
        "name_query": {"type": "string", "description": "Item 编码或名称关键词"},
    },
    group="items_catalog",
    short_description="List item master records with optional code or name filtering.",
)
def list_items(name_query=""):
    return _format_json(_run_site_action("list_items", {"name_query": name_query}))


@erpnext_tool(
    "get_item",
    "获取单个 Item 的详情。",
    {
        "item_code": {"type": "string", "description": "Item 编码"},
    },
    required=["item_code"],
    group="items_catalog",
    short_description="Read one item master record by item code.",
)
def get_item(item_code):
    payload = _run_site_action("get_item", {"item_code": item_code})
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到 Item: {item_code}")
    return _format_json(payload)


@erpnext_tool(
    "create_item",
    "创建新的 Item 主数据。",
    {
        "item_code": {"type": "string", "description": "Item 编码"},
        "item_name": {"type": "string", "description": "Item 名称"},
        "item_group": {"type": "string", "description": "Item Group，默认 All Item Groups"},
        "stock_uom": {"type": "string", "description": "计量单位，默认 Nos"},
        "is_stock_item": {"type": "integer", "description": "是否为库存商品，0 或 1"},
    },
    required=["item_code", "item_name"],
    is_write=True,
    group="items_catalog",
    short_description="Create a new item master record.",
)
def create_item(item_code, item_name, item_group="All Item Groups", stock_uom="Nos", is_stock_item=0):
    return _format_json(
        _run_site_action(
            "create_item",
            {
                "item_code": item_code,
                "item_name": item_name,
                "item_group": item_group,
                "stock_uom": stock_uom,
                "is_stock_item": is_stock_item,
            },
        )
    )


@erpnext_tool(
    "list_purchase_invoices",
    "列出采购发票，可按状态或供应商筛选。",
    {
        "status": {"type": "string", "description": "状态，如 Unpaid、Paid、Overdue"},
        "supplier": {"type": "string", "description": "供应商名称"},
    },
    group="purchase_invoices",
    short_description="List purchase invoices with optional status or supplier filters.",
)
def list_purchase_invoices(status="", supplier=""):
    return _format_json(
        _run_site_action("list_purchase_invoices", {"status": status, "supplier": supplier})
    )


@erpnext_tool(
    "list_supplier_purchase_invoices",
    "列出某个供应商的采购发票，可按状态筛选。",
    {
        "supplier_name": {"type": "string", "description": "供应商名称"},
        "status": {"type": "string", "description": "状态，如 Unpaid、Paid、Overdue"},
    },
    required=["supplier_name"],
    group="purchase_invoices",
    short_description="List purchase invoices for one supplier with optional status filtering.",
)
def list_supplier_purchase_invoices(supplier_name, status=""):
    return _format_json(
        _run_site_action(
            "list_supplier_purchase_invoices",
            {"supplier_name": supplier_name, "status": status},
        )
    )


@erpnext_tool(
    "list_overdue_purchase_invoices",
    "列出当前已逾期且仍有未结清金额的采购发票。",
    {},
    group="purchase_invoices",
    short_description="List overdue purchase invoices that still have outstanding balance.",
)
def list_overdue_purchase_invoices():
    return _format_json(_run_site_action("list_overdue_purchase_invoices"))


@erpnext_tool(
    "get_purchase_invoice",
    "获取单个采购发票详情，包括明细行和备注。",
    {
        "purchase_invoice_name": {"type": "string", "description": "采购发票编号"},
    },
    required=["purchase_invoice_name"],
    group="purchase_invoices",
    short_description="Read one purchase invoice with items and comments.",
)
def get_purchase_invoice(purchase_invoice_name):
    payload = _run_site_action("get_purchase_invoice", {"purchase_invoice_name": purchase_invoice_name})
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到采购发票: {purchase_invoice_name}")
    return _format_json(payload)


@erpnext_tool(
    "list_purchase_invoice_comments",
    "列出某张采购发票的备注历史。",
    {
        "purchase_invoice_name": {"type": "string", "description": "采购发票编号"},
    },
    required=["purchase_invoice_name"],
    group="purchase_invoice_comments",
    short_description="List comment history attached to one purchase invoice.",
)
def list_purchase_invoice_comments(purchase_invoice_name):
    return _format_json(
        _run_site_action(
            "list_purchase_invoice_comments",
            {"purchase_invoice_name": purchase_invoice_name},
        )
    )


@erpnext_tool(
    "add_purchase_invoice_comment",
    "为采购发票追加一条备注。",
    {
        "purchase_invoice_name": {"type": "string", "description": "采购发票编号"},
        "comment": {"type": "string", "description": "备注内容"},
        "author": {"type": "string", "description": "备注作者"},
    },
    required=["purchase_invoice_name", "comment"],
    is_write=True,
    group="purchase_invoice_comments",
    short_description="Append an AP note to an existing purchase invoice.",
)
def add_purchase_invoice_comment(purchase_invoice_name, comment, author="ap-bot"):
    return _format_json(
        _run_site_action(
            "add_purchase_invoice_comment",
            {
                "purchase_invoice_name": purchase_invoice_name,
                "comment": comment,
                "author": author,
            },
        )
    )


@erpnext_tool(
    "update_purchase_invoice_due_date",
    "更新采购发票的到期日。",
    {
        "purchase_invoice_name": {"type": "string", "description": "采购发票编号"},
        "due_date": {"type": "string", "description": "新的到期日，如 2026-04-30"},
    },
    required=["purchase_invoice_name", "due_date"],
    is_write=True,
    group="purchase_invoice_updates",
    short_description="Change the due date of one existing purchase invoice.",
)
def update_purchase_invoice_due_date(purchase_invoice_name, due_date):
    return _format_json(
        _run_site_action(
            "update_purchase_invoice_due_date",
            {"purchase_invoice_name": purchase_invoice_name, "due_date": due_date},
        )
    )


@erpnext_tool(
    "cancel_purchase_invoice",
    "取消采购发票。已提交的采购发票才能取消。",
    {
        "purchase_invoice_name": {"type": "string", "description": "采购发票编号"},
    },
    required=["purchase_invoice_name"],
    is_write=True,
    group="purchase_invoice_updates",
    short_description="Cancel a submitted purchase invoice.",
)
def cancel_purchase_invoice(purchase_invoice_name):
    return _format_json(
        _run_site_action(
            "cancel_purchase_invoice",
            {"purchase_invoice_name": purchase_invoice_name},
        )
    )


@erpnext_tool(
    "create_purchase_invoice",
    "创建采购发票。",
    {
        "supplier": {"type": "string", "description": "供应商名称"},
        "items": {
            "type": "array",
            "items": {"type": "object"},
            "description": "采购发票行项目列表，每项包含 item_code、qty、rate，可选 item_name",
        },
        "due_date": {"type": "string", "description": "到期日期，如 2026-04-15"},
    },
    required=["supplier", "items"],
    is_write=True,
    group="purchase_invoices",
    short_description="Create a submitted purchase invoice for a supplier.",
)
def create_purchase_invoice(supplier, items, due_date=""):
    return _format_json(
        _run_site_action(
            "create_purchase_invoice",
            {
                "supplier": supplier,
                "items": items,
                "due_date": due_date,
            },
        )
    )


@erpnext_tool(
    "list_companies",
    "列出当前 ERPNext 站点中的公司。",
    {},
    group="reference_data",
    short_description="List configured companies in the ERPNext site.",
)
def list_companies():
    return _format_json(_run_site_action("list_companies"))


@erpnext_tool(
    "list_payment_modes",
    "列出当前可用的付款方式。",
    {},
    group="reference_data",
    short_description="List available payment modes such as Cash or Wire Transfer.",
)
def list_payment_modes():
    return _format_json(_run_site_action("list_payment_modes"))
