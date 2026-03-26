#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta


def _numeric_suffix(value, prefix):
    match = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", str(value or ""))
    if not match:
        raise ValueError(f"invalid id: {value}")
    return int(match.group(1))


def _split_name(name):
    parts = [part for part in str(name or "").strip().split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def _sql_literal(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def _run_mysql(db_container, root_password, db_name, sql):
    cmd = [
        "docker",
        "exec",
        "-i",
        db_container,
        "mysql",
        "-uroot",
        f"-p{root_password}",
        db_name,
    ]
    result = subprocess.run(cmd, input=sql, text=True, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown mysql error"
        raise RuntimeError(detail)


def _seed_patient(patient):
    pid = _numeric_suffix(patient["id"], "PT")
    pubpid = patient["id"]
    fname, lname = _split_name(patient.get("name", ""))
    dob = patient.get("dob", "")
    return f"""
DELETE FROM pnotes WHERE pid = {pid};
DELETE FROM openemr_postcalendar_events WHERE pc_pid = '{pid}';
DELETE FROM patient_data WHERE pid = {pid};
INSERT INTO patient_data (pid, pubpid, fname, lname, DOB, sex, title, language, country_code, phone_home, email, status)
VALUES ({pid}, {_sql_literal(pubpid)}, {_sql_literal(fname)}, {_sql_literal(lname)}, {_sql_literal(dob)}, '', '', '', 'US', '', '', '');
"""


def _seed_note(patient_id, note):
    pid = _numeric_suffix(patient_id, "PT")
    author = note.get("author", "system")
    body = note.get("body", "")
    title = note.get("title", "Chart note")
    return (
        "INSERT INTO pnotes (date, body, pid, user, groupname, activity, authorized, title, assigned_to, deleted, message_status, is_msg_encrypted) "
        f"VALUES (NOW(), {_sql_literal(body)}, {pid}, {_sql_literal(author)}, 'Default', 1, 1, {_sql_literal(title)}, '', 0, 'New', 0);"
    )


def _seed_appointment(appointment):
    appointment_num = _numeric_suffix(appointment["id"], "APT")
    patient_num = _numeric_suffix(appointment["patient_id"], "PT")
    category = 10 if "new patient" in str(appointment.get("reason", "")).lower() else 9
    start_dt = datetime.strptime(f"{appointment['date']} {appointment['time']}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=30)
    provider = appointment.get("provider", "")
    reason = appointment.get("reason", "")
    status = appointment.get("status", "scheduled")
    return (
        "INSERT INTO openemr_postcalendar_events "
        "(pc_eid, pc_catid, pc_multiple, pc_aid, pc_pid, pc_topic, pc_eventDate, pc_endDate, pc_duration, pc_recurrtype, pc_recurrfreq, "
        "pc_startTime, pc_endTime, pc_alldayevent, pc_eventstatus, pc_sharing, pc_apptstatus, pc_prefcatid, pc_facility, pc_sendalertsms, "
        "pc_sendalertemail, pc_billing_location, pc_room, pc_title, pc_hometext) "
        "VALUES "
        f"({appointment_num}, {category}, 0, 'admin', '{patient_num}', 1, {_sql_literal(appointment['date'])}, {_sql_literal(appointment['date'])}, 1800, 0, 0, "
        f"{_sql_literal(start_dt.strftime('%H:%M:%S'))}, {_sql_literal(end_dt.strftime('%H:%M:%S'))}, 0, 0, 0, {_sql_literal(status)}, 0, 0, 'NO', 'NO', 0, '', "
        f"{_sql_literal(reason)}, {_sql_literal(provider)});"
    )


def main():
    if len(sys.argv) != 5:
        raise SystemExit("usage: seed_openemr_data.py <manifest> <db_container> <db_name> <root_password>")

    manifest_path, db_container, db_name, root_password = sys.argv[1:5]
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    statements = [
        "DELETE FROM pnotes WHERE pid IN (100, 101, 102, 103, 104, 105);",
        "DELETE FROM openemr_postcalendar_events WHERE pc_eid IN (100, 101, 102, 103, 104, 105);",
        "DELETE FROM patient_data WHERE pid IN (100, 101, 102, 103, 104, 105);",
    ]

    for patient in manifest.get("patients", []):
        statements.append(_seed_patient(patient))
    for patient in manifest.get("patients", []):
        for note in patient.get("notes", []):
            statements.append(_seed_note(patient["id"], note))
    for appointment in manifest.get("appointments", []):
        statements.append(_seed_appointment(appointment))

    _run_mysql(db_container, root_password, db_name, "\n".join(statements) + "\n")


if __name__ == "__main__":
    main()
