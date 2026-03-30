"""
ERPNext 真实站点工具。
"""

import json
import os
import subprocess
import base64

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
    "列出发票，可按状态或客户筛选。",
    {
        "status": {"type": "string", "description": "状态，如 Unpaid、Paid、Overdue"},
        "customer": {"type": "string", "description": "客户名称"},
    },
)
def list_invoices(status="", customer=""):
    return _format_json(_run_site_action("list_invoices", {"status": status, "customer": customer}))


@erpnext_tool(
    "get_invoice",
    "获取单个发票详情。",
    {
        "invoice_name": {"type": "string", "description": "发票编号"},
    },
)
def get_invoice(invoice_name):
    payload = _run_site_action("get_invoice", {"invoice_name": invoice_name})
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到发票: {invoice_name}")
    return _format_json(payload)


@erpnext_tool(
    "list_payments",
    "列出付款记录。",
    {
        "status": {"type": "string", "description": "状态，如 Draft、Submitted"},
    },
)
def list_payments(status=""):
    return _format_json(_run_site_action("list_payments", {"status": status}))


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
    "update_invoice_status",
    "更新发票状态。当前支持把销售发票标记为 Paid。",
    {
        "invoice_name": {"type": "string", "description": "发票编号"},
        "status": {"type": "string", "description": "新状态，如 Paid"},
    },
    required=["invoice_name", "status"],
    is_write=True,
)
def update_invoice_status(invoice_name, status):
    return _format_json(
        _run_site_action(
            "update_invoice_status",
            {
                "invoice_name": invoice_name,
                "status": status,
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
)
def delete_payment_entry(payment_name):
    return _format_json(_run_site_action("delete_payment_entry", {"payment_name": payment_name}))


# ---------------------------------------------------------------------------
# New tools based on ERPNext Frappe REST API
# ---------------------------------------------------------------------------

@erpnext_tool(
    "list_customers",
    "列出客户，可按名称筛选。",
    {
        "name_query": {"type": "string", "description": "客户名称关键词"},
    },
)
def list_customers(name_query=""):
    return _format_json(_run_site_action("list_customers", {"name_query": name_query}))


@erpnext_tool(
    "get_customer",
    "获取单个客户详情。",
    {
        "customer_name": {"type": "string", "description": "客户名称"},
    },
    required=["customer_name"],
)
def get_customer(customer_name):
    payload = _run_site_action("get_customer", {"customer_name": customer_name})
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到客户: {customer_name}")
    return _format_json(payload)


@erpnext_tool(
    "get_payment",
    "获取单个付款记录详情。",
    {
        "payment_name": {"type": "string", "description": "付款记录编号"},
    },
    required=["payment_name"],
)
def get_payment(payment_name):
    payload = _run_site_action("get_payment", {"payment_name": payment_name})
    if not payload:
        raise ToolExecutionError(f"[错误] 找不到付款记录: {payment_name}")
    return _format_json(payload)


@erpnext_tool(
    "create_invoice",
    "创建销售发票。",
    {
        "customer": {"type": "string", "description": "客户名称"},
        "items": {
            "type": "array",
            "items": {"type": "object"},
            "description": "发票行项目列表，每项包含 item_code、qty、rate",
        },
        "due_date": {"type": "string", "description": "到期日期，如 2026-04-15"},
    },
    required=["customer", "items"],
    is_write=True,
)
def create_invoice(customer, items, due_date=""):
    return _format_json(_run_site_action("create_invoice", {
        "customer": customer,
        "items": items,
        "due_date": due_date,
    }))


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
)
def create_payment_entry(invoice_name, amount, mode_of_payment="Cash"):
    return _format_json(_run_site_action("create_payment_entry", {
        "invoice_name": invoice_name,
        "amount": amount,
        "mode_of_payment": mode_of_payment,
    }))


@erpnext_tool(
    "cancel_invoice",
    "取消销售发票。已提交的发票才能取消。",
    {
        "invoice_name": {"type": "string", "description": "发票编号"},
    },
    required=["invoice_name"],
    is_write=True,
)
def cancel_invoice(invoice_name):
    return _format_json(_run_site_action("cancel_invoice", {"invoice_name": invoice_name}))
