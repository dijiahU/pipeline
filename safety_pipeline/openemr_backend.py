import json
import os
import shutil
import subprocess
import tempfile
import time

from .backend_abc import EnvironmentBackend
from .settings import REPO_ROOT, reload_runtime_env


class OpenEMRBackend(EnvironmentBackend):
    def __init__(self):
        self._openemr_tools = None
        self._active_try_checkpoint = None

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

    def _compose_file(self):
        return os.environ.get(
            "OPENEMR_COMPOSE_FILE",
            os.path.join(REPO_ROOT, "docker", "openemr", "docker-compose.yml"),
        )

    def _shared_dir(self):
        return os.environ.get("OPENEMR_SHARED_DIR", os.path.join(REPO_ROOT, "docker", "openemr", "shared"))

    def _sites_dir(self):
        return os.path.join(self._shared_dir(), "sites")

    def _base_url(self):
        return os.environ.get("OPENEMR_BASE_URL", "http://localhost:8083").rstrip("/")

    def _db_container(self):
        return os.environ.get("OPENEMR_DB_CONTAINER", "pipeline-openemr-mysql")

    def _db_root_password(self):
        return os.environ.get("OPENEMR_DB_ROOT_PASSWORD", "root")

    def _db_name(self):
        return os.environ.get("OPENEMR_DB_NAME", "openemr")

    def _run_command(self, cmd):
        result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{detail}")
        return result.stdout.strip()

    def _wait_for_openemr(self, timeout=360, interval=5):
        import requests

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = requests.get(f"{self._base_url()}/interface/login/login.php?site=default", timeout=10)
                if resp.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(interval)
        raise RuntimeError("Timed out waiting for OpenEMR HTTP service to become ready")

    def _wait_for_db(self, timeout=300, interval=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Health.Status}}", self._db_container()],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip() == "healthy":
                return
            time.sleep(interval)
        raise RuntimeError("Timed out waiting for the OpenEMR database health check")

    def _stop_stack(self):
        subprocess.run(
            ["docker", "compose", "-f", self._compose_file(), "stop"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def _start_runtime_stack(self):
        self._run_command(["docker", "compose", "-f", self._compose_file(), "up", "-d", "openemr-db", "openemr"])
        self._wait_for_openemr()

    def _dump_database(self, dump_path):
        basename = os.path.basename(dump_path)
        self._run_command(
            [
                "docker",
                "exec",
                self._db_container(),
                "bash",
                "-lc",
                f"mysqldump -uroot -p{self._db_root_password()} --single-transaction '{self._db_name()}' > /tmp/{basename}",
            ]
        )
        self._run_command(["docker", "cp", f"{self._db_container()}:/tmp/{basename}", dump_path])

    def _restore_database(self, dump_path):
        basename = os.path.basename(dump_path)
        self._run_command(["docker", "cp", dump_path, f"{self._db_container()}:/tmp/{basename}"])
        self._run_command(
            [
                "docker",
                "exec",
                self._db_container(),
                "bash",
                "-lc",
                (
                    f"mysql -uroot -p{self._db_root_password()} "
                    f"-e 'DROP DATABASE IF EXISTS `{self._db_name()}`; "
                    f"CREATE DATABASE `{self._db_name()}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;'"
                ),
            ]
        )
        self._run_command(
            [
                "docker",
                "exec",
                self._db_container(),
                "bash",
                "-lc",
                f"mysql -uroot -p{self._db_root_password()} '{self._db_name()}' < /tmp/{basename}",
            ]
        )

    def _create_try_checkpoint(self):
        if self._active_try_checkpoint is not None:
            raise RuntimeError("An uncleared try snapshot already exists.")
        checkpoint_root = tempfile.mkdtemp(prefix="openemr-try-backup-")
        sites_snapshot_dir = os.path.join(checkpoint_root, "sites")
        dump_path = os.path.join(checkpoint_root, "openemr.sql")
        self._stop_stack()
        try:
            shutil.copytree(self._sites_dir(), sites_snapshot_dir)
            self._run_command(["docker", "compose", "-f", self._compose_file(), "up", "-d", "openemr-db"])
            self._wait_for_db()
            self._dump_database(dump_path)
        finally:
            self._start_runtime_stack()
        checkpoint = {
            "kind": "sites_plus_mysqldump",
            "checkpoint_root": checkpoint_root,
            "sites_snapshot_dir": sites_snapshot_dir,
            "dump_path": dump_path,
        }
        self._active_try_checkpoint = checkpoint
        return checkpoint

    def _restore_from_checkpoint(self, checkpoint):
        if not checkpoint:
            return
        self._stop_stack()
        try:
            shutil.rmtree(self._sites_dir(), ignore_errors=True)
            os.makedirs(self._shared_dir(), exist_ok=True)
            shutil.copytree(checkpoint["sites_snapshot_dir"], self._sites_dir(), dirs_exist_ok=True)
            self._run_command(["docker", "compose", "-f", self._compose_file(), "up", "-d", "openemr-db"])
            self._wait_for_db()
            self._restore_database(checkpoint["dump_path"])
        finally:
            self._start_runtime_stack()

    def _discard_checkpoint_data(self, checkpoint):
        if not checkpoint:
            return
        shutil.rmtree(checkpoint.get("checkpoint_root", ""), ignore_errors=True)

    def _build_try_summary(self, name, args, exec_result, state_changed):
        affected_sample = []
        if "patient_id" in args:
            affected_sample.append(str(args.get("patient_id")))
        if "appointment_id" in args:
            affected_sample.append(str(args.get("appointment_id")))
        if "encounter_id" in args:
            affected_sample.append(str(args.get("encounter_id")))
        if "medication_id" in args:
            affected_sample.append(str(args.get("medication_id")))
        if "policy_id" in args:
            affected_sample.append(str(args.get("policy_id")))
        return {
            "exec_status": "success",
            "state_changed": state_changed,
            "affected_objects_count": len([item for item in affected_sample if item]),
            "affected_objects_sample": [item for item in affected_sample if item],
            "unexpected_side_effect": False,
            "observed_effects": [f"tool_try executed {name} for real"] if state_changed else [f"Read-only tool {name} executed successfully"],
            "summary": (
                f"tool_try executed {name} for real. The current result can be rolled back during unsafe->ask_human."
                if state_changed
                else f"{name} completed successfully with no side effects."
            ),
            "exec_result": exec_result,
        }

    def run_try(self, name, args):
        ot = self._get_openemr_tools()
        is_write_tool = name in set(self.get_write_tool_names())

        if is_write_tool:
            self._create_try_checkpoint()
            try:
                exec_result = ot.call_tool(name, args)
            except Exception:
                try:
                    self.rollback_try()
                except Exception:
                    self._active_try_checkpoint = None
                raise
            return {
                "summary": self._build_try_summary(name, args, exec_result, state_changed=True),
                "exec_result_raw": exec_result,
            }

        exec_result = ot.call_tool(name, args)
        return {
            "summary": self._build_try_summary(name, args, exec_result, state_changed=False),
            "exec_result_raw": exec_result,
        }

    def commit_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def rollback_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        try:
            self._restore_from_checkpoint(checkpoint)
        finally:
            self._active_try_checkpoint = None
            self._discard_checkpoint_data(checkpoint)
        return True

    def discard_try(self):
        if self._active_try_checkpoint is None:
            return False
        checkpoint = self._active_try_checkpoint
        self._active_try_checkpoint = None
        self._discard_checkpoint_data(checkpoint)
        return True

    def reset(self):
        script_path = os.path.join(REPO_ROOT, "scripts", "reset_openemr_env.sh")
        try:
            self.discard_try()
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
