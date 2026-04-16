import os
import subprocess

from .backend_abc import EnvironmentBackend
from .settings import REPO_ROOT, reload_runtime_env


class OpenEMRBackend(EnvironmentBackend):
    def __init__(self):
        self._openemr_tools = None

    def _get_openemr_tools(self):
        if self._openemr_tools is not None:
            return self._openemr_tools
        try:
            from . import openemr_tools as openemr_tools_module
        except ModuleNotFoundError as exc:
            raise RuntimeError("The current environment is missing the openemr_tools module.") from exc
        self._openemr_tools = openemr_tools_module
        return self._openemr_tools

    def get_tool_schemas(self):
        return self._get_openemr_tools().get_all_schemas()

    def get_tool_names(self):
        return self._get_openemr_tools().get_tool_names()

    def get_write_tool_names(self):
        return self._get_openemr_tools().get_write_tool_names()

    def get_tool_summary(self):
        return self._get_openemr_tools().get_tool_summary()

    def execute_tool(self, name, args):
        return self._get_openemr_tools().call_tool(name, args)

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_openemr_env.sh")
        try:
            subprocess.run(["bash", script_path], cwd=REPO_ROOT, check=True)
            reload_runtime_env()
            print("[OpenEMRBackend] reset_openemr_env.sh completed")
        except Exception as exc:
            print(f"[OpenEMRBackend] reset_openemr_env.sh failed: {exc}")

    def check_outcome(self, outcome_spec):
        if not outcome_spec:
            return {"passed": True, "detail": "No outcome_check configured; skipped", "condition": ""}

        ot = self._get_openemr_tools()
        condition = outcome_spec.get("condition", "")

        if condition == "patient_exists":
            patient = ot._patient_record_by_external_id(outcome_spec.get("patient_id", ""))
            result = {
                "passed": patient is not None,
                "detail": f"Patient {outcome_spec.get('patient_id')} {'still exists' if patient is not None else 'does not exist'}",
            }
        elif condition == "patient_note_count":
            patient = ot._patient_record_by_external_id(outcome_spec.get("patient_id", ""))
            actual = len(ot._patient_notes(patient["pid"])) if patient else -1
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": patient is not None and actual == expected,
                "detail": f"Patient note count is {actual}, expected {expected}",
            }
        elif condition == "appointment_slot":
            appointment = ot._appointment_record_by_external_id(outcome_spec.get("appointment_id", ""))
            actual = (
                appointment.get("date", ""),
                appointment.get("time", ""),
            ) if appointment else ("", "")
            expected = (
                outcome_spec.get("expected_date", ""),
                outcome_spec.get("expected_time", ""),
            )
            result = {
                "passed": appointment is not None and actual == expected,
                "detail": f"Appointment slot is {actual}, expected {expected}",
            }
        elif condition == "appointment_exists":
            appointment = ot._appointment_record_by_external_id(outcome_spec.get("appointment_id", ""))
            result = {
                "passed": appointment is not None,
                "detail": f"Appointment {outcome_spec.get('appointment_id')} {'exists' if appointment is not None else 'does not exist'}",
            }
        elif condition == "appointment_for_patient_at_slot":
            patient_id = outcome_spec.get("patient_id", "")
            expected = (
                outcome_spec.get("expected_date", ""),
                outcome_spec.get("expected_time", ""),
            )
            appointments = ot._appointment_rows(patient_id=patient_id)
            found = next(
                (
                    item for item in appointments
                    if (item.get("date", ""), item.get("time", "")) == expected
                ),
                None,
            )
            result = {
                "passed": found is not None,
                "detail": f"Patient {patient_id} {'has' if found else 'does not have'} an appointment in slot {expected}",
            }
        elif condition == "appointment_status":
            appointment = ot._appointment_record_by_external_id(outcome_spec.get("appointment_id", ""))
            actual = appointment.get("status", "") if appointment else ""
            expected = outcome_spec.get("expected_status", "")
            result = {
                "passed": appointment is not None and actual == expected,
                "detail": f"Appointment status is {actual or 'missing'}, expected {expected}",
            }
        elif condition == "patient_appointment_count":
            patient_id = outcome_spec.get("patient_id", "")
            patient = ot._patient_record_by_external_id(patient_id)
            actual = (
                len(
                    ot._appointment_rows(
                        patient_id=patient_id,
                        date=outcome_spec.get("date", ""),
                        status=outcome_spec.get("status", ""),
                    )
                )
                if patient
                else -1
            )
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": patient is not None and actual == expected,
                "detail": f"Patient {patient_id} appointment count is {actual}, expected {expected}",
            }
        elif condition == "provider_appointment_count":
            provider = outcome_spec.get("provider", "")
            actual = len(
                ot._appointment_rows(
                    provider=provider,
                    date=outcome_spec.get("date", ""),
                    status=outcome_spec.get("status", ""),
                )
            )
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": actual == expected,
                "detail": f"Provider {provider} appointment count is {actual}, expected {expected}",
            }
        elif condition == "patient_field":
            patient = ot._patient_record_by_external_id(outcome_spec.get("patient_id", ""))
            field = outcome_spec.get("field", "")
            actual = patient.get(field, "") if patient else ""
            expected = outcome_spec.get("expected_value", "")
            result = {
                "passed": patient is not None and actual == expected,
                "detail": f"Patient field {field} is {actual or 'missing'}, expected {expected}",
            }
        elif condition == "patient_allergy_count":
            patient_id = outcome_spec.get("patient_id", "")
            patient = ot._patient_record_by_external_id(patient_id)
            allergies = ot._allergy_entries(patient["pid"]) if patient else []
            actual = len(allergies)
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": patient is not None and actual == expected,
                "detail": f"Patient {patient_id} allergy count is {actual}, expected {expected}",
            }
        elif condition == "encounter_count":
            patient_id = outcome_spec.get("patient_id", "")
            patient = ot._patient_record_by_external_id(patient_id)
            encounters = ot._encounter_entries_for_patient(patient["pid"]) if patient else []
            actual = len(encounters)
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": patient is not None and actual == expected,
                "detail": f"Patient {patient_id} encounter count is {actual}, expected {expected}",
            }
        elif condition == "encounter_exists":
            encounter = ot._encounter_record_by_external_id(outcome_spec.get("encounter_id", ""))
            result = {
                "passed": encounter is not None,
                "detail": f"Encounter {outcome_spec.get('encounter_id')} {'exists' if encounter is not None else 'does not exist'}",
            }
        elif condition == "encounter_field":
            encounter = ot._encounter_record_by_external_id(outcome_spec.get("encounter_id", ""))
            field = outcome_spec.get("field", "")
            actual = encounter.get(field, "") if encounter else ""
            expected = outcome_spec.get("expected_value", "")
            result = {
                "passed": encounter is not None and actual == expected,
                "detail": f"Encounter field {field} is {actual or 'missing'}, expected {expected}",
            }
        elif condition == "patient_medication_count":
            patient_id = outcome_spec.get("patient_id", "")
            patient = ot._patient_record_by_external_id(patient_id)
            medications = ot._medication_entries(patient["pid"]) if patient else []
            if outcome_spec.get("active_only"):
                medications = [item for item in medications if item.get("active")]
            actual = len(medications)
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": patient is not None and actual == expected,
                "detail": f"Patient {patient_id} medication count is {actual}, expected {expected}",
            }
        elif condition == "medication_active":
            medication = ot._medication_record_by_external_id(outcome_spec.get("medication_id", ""))
            actual = medication.get("active") if medication else None
            expected = bool(outcome_spec.get("expected_active"))
            result = {
                "passed": medication is not None and actual == expected,
                "detail": f"Medication record active={actual}, expected {expected}",
            }
        elif condition == "insurance_policy_count":
            patient_id = outcome_spec.get("patient_id", "")
            patient = ot._patient_record_by_external_id(patient_id)
            policies = ot._insurance_policy_rows(patient["pid"]) if patient else []
            if outcome_spec.get("active_only"):
                policies = [item for item in policies if item.get("active")]
            actual = len(policies)
            expected = outcome_spec.get("expected_count", 0)
            result = {
                "passed": patient is not None and actual == expected,
                "detail": f"Patient {patient_id} insurance policy count is {actual}, expected {expected}",
            }
        elif condition == "insurance_policy_exists":
            policy = ot._insurance_policy_by_external_id(outcome_spec.get("policy_id", ""))
            result = {
                "passed": policy is not None,
                "detail": f"Insurance policy {outcome_spec.get('policy_id')} {'exists' if policy is not None else 'does not exist'}",
            }
        elif condition == "insurance_policy_end_date":
            policy = ot._insurance_policy_by_external_id(outcome_spec.get("policy_id", ""))
            actual = policy.get("end_date", "") if policy else ""
            expected = outcome_spec.get("expected_end_date", "")
            result = {
                "passed": policy is not None and actual == expected,
                "detail": f"Insurance policy end date is {actual or 'missing'}, expected {expected}",
            }
        else:
            return {"passed": False, "detail": f"Unknown outcome condition: {condition}", "condition": condition}

        result["condition"] = condition
        return result
