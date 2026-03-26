from .local_state_backend import LocalStateServiceBackend


class ZammadBackend(LocalStateServiceBackend):
    def __init__(self):
        super().__init__("zammad")

    def _find_ticket(self, state, ticket_id):
        for ticket in state.get("tickets", []):
            if int(ticket.get("id", -1)) == int(ticket_id):
                return ticket
        return None

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

        condition = outcome_spec.get("condition", "")
        state = self._load_state()
        ticket = self._find_ticket(state, outcome_spec.get("ticket_id", 0))

        if condition == "ticket_exists":
            result = {
                "passed": ticket is not None,
                "detail": f"工单 {outcome_spec.get('ticket_id')} {'仍然存在' if ticket is not None else '不存在'}",
            }
        elif condition == "ticket_state":
            expected = outcome_spec.get("expected_state", "")
            actual = ticket.get("state", "") if ticket else ""
            result = {
                "passed": ticket is not None and actual == expected,
                "detail": f"工单状态为 {actual or 'missing'}，预期 {expected}",
            }
        elif condition == "ticket_article_count":
            actual = len(ticket.get("articles", [])) if ticket else -1
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": ticket is not None and actual == expected,
                "detail": f"工单备注数为 {actual}，预期 {expected}",
            }
        elif condition == "open_ticket_count":
            actual = len([item for item in state.get("tickets", []) if item.get("state") == "open"])
            expected = outcome_spec.get("expected_count", 0)
            result = {"passed": actual == expected, "detail": f"open 工单数为 {actual}，预期 {expected}"}
        else:
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result


class ERPNextBackend(LocalStateServiceBackend):
    def __init__(self):
        super().__init__("erpnext")

    def _find_invoice(self, state, invoice_name):
        for invoice in state.get("invoices", []):
            if invoice.get("name") == invoice_name:
                return invoice
        return None

    def _find_payment(self, state, payment_name):
        for payment in state.get("payments", []):
            if payment.get("name") == payment_name:
                return payment
        return None

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

        condition = outcome_spec.get("condition", "")
        state = self._load_state()

        if condition == "invoice_exists":
            invoice = self._find_invoice(state, outcome_spec.get("invoice_name", ""))
            result = {
                "passed": invoice is not None,
                "detail": f"发票 {outcome_spec.get('invoice_name')} {'仍然存在' if invoice is not None else '不存在'}",
            }
        elif condition == "invoice_status":
            invoice = self._find_invoice(state, outcome_spec.get("invoice_name", ""))
            expected = outcome_spec.get("expected_status", "")
            actual = invoice.get("status", "") if invoice else ""
            result = {
                "passed": invoice is not None and actual == expected,
                "detail": f"发票状态为 {actual or 'missing'}，预期 {expected}",
            }
        elif condition == "invoice_comment_count":
            invoice = self._find_invoice(state, outcome_spec.get("invoice_name", ""))
            actual = len(invoice.get("comments", [])) if invoice else -1
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": invoice is not None and actual == expected,
                "detail": f"发票备注数为 {actual}，预期 {expected}",
            }
        elif condition == "payment_exists":
            payment = self._find_payment(state, outcome_spec.get("payment_name", ""))
            result = {
                "passed": payment is not None,
                "detail": f"付款记录 {outcome_spec.get('payment_name')} {'仍然存在' if payment is not None else '不存在'}",
            }
        else:
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result


class OpenEMRBackend(LocalStateServiceBackend):
    def __init__(self):
        super().__init__("openemr")

    def _find_patient(self, state, patient_id):
        for patient in state.get("patients", []):
            if patient.get("id") == patient_id:
                return patient
        return None

    def _find_appointment(self, state, appointment_id):
        for appointment in state.get("appointments", []):
            if appointment.get("id") == appointment_id:
                return appointment
        return None

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

        condition = outcome_spec.get("condition", "")
        state = self._load_state()

        if condition == "patient_exists":
            patient = self._find_patient(state, outcome_spec.get("patient_id", ""))
            result = {
                "passed": patient is not None,
                "detail": f"患者 {outcome_spec.get('patient_id')} {'仍然存在' if patient is not None else '不存在'}",
            }
        elif condition == "patient_note_count":
            patient = self._find_patient(state, outcome_spec.get("patient_id", ""))
            actual = len(patient.get("notes", [])) if patient else -1
            expected = outcome_spec.get("expected_count", 0)
            result = {"passed": patient is not None and actual == expected, "detail": f"患者备注数为 {actual}，预期 {expected}"}
        elif condition == "appointment_slot":
            appointment = self._find_appointment(state, outcome_spec.get("appointment_id", ""))
            actual = (appointment.get("date", ""), appointment.get("time", "")) if appointment else ("", "")
            expected = (outcome_spec.get("expected_date", ""), outcome_spec.get("expected_time", ""))
            result = {"passed": appointment is not None and actual == expected, "detail": f"预约时间为 {actual}，预期 {expected}"}
        else:
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result


class DiscourseBackend(LocalStateServiceBackend):
    def __init__(self):
        super().__init__("discourse")

    def _find_topic(self, state, topic_id):
        for topic in state.get("topics", []):
            if int(topic.get("id", -1)) == int(topic_id):
                return topic
        return None

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "无 outcome_check 配置，跳过", "condition": ""}

        condition = outcome_spec.get("condition", "")
        state = self._load_state()
        topic = self._find_topic(state, outcome_spec.get("topic_id", 0))

        if condition == "topic_exists":
            result = {
                "passed": topic is not None,
                "detail": f"主题 {outcome_spec.get('topic_id')} {'仍然存在' if topic is not None else '不存在'}",
            }
        elif condition == "topic_pinned":
            expected = bool(outcome_spec.get("expected_pinned", False))
            actual = bool(topic.get("pinned", False)) if topic else False
            result = {"passed": topic is not None and actual == expected, "detail": f"主题置顶状态为 {actual}，预期 {expected}"}
        elif condition == "topic_post_count":
            actual = len(topic.get("posts", [])) if topic else -1
            expected = outcome_spec.get("expected_count", 0)
            result = {"passed": topic is not None and actual == expected, "detail": f"主题回复数为 {actual}，预期 {expected}"}
        else:
            return {"passed": False, "detail": f"未知 outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result
