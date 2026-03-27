import json
import os
import re
import subprocess
from datetime import datetime, timedelta

from .exceptions import ToolExecutionError
from .service_tools import ServiceToolRegistry


_REGISTRY = ServiceToolRegistry(service_id="openemr")


def openemr_tool(name, description, params, required=None, is_write=False):
    return _REGISTRY.register(
        name=name,
        description=description,
        params=params,
        required=required,
        is_write=is_write,
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


def _db_container():
    return os.environ.get("OPENEMR_DB_CONTAINER", "pipeline-openemr-mysql")


def _db_name():
    return os.environ.get("OPENEMR_DB_NAME", "openemr")


def _db_root_password():
    return os.environ.get("OPENEMR_DB_ROOT_PASSWORD", "root")


def _run_mysql(sql, *, expect_rows=False):
    cmd = [
        "docker",
        "exec",
        "-i",
        _db_container(),
        "mysql",
        "-uroot",
        f"-p{_db_root_password()}",
        _db_name(),
    ]
    if expect_rows:
        cmd.extend(["--batch", "--raw", "--skip-column-names"])
    result = subprocess.run(cmd, input=sql, text=True, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown mysql error"
        raise ToolExecutionError(f"[OpenEMR SQL 错误] {detail}")
    return result.stdout


def _query_rows(sql, columns):
    output = _run_mysql(sql, expect_rows=True).strip()
    if not output:
        return []
    rows = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < len(columns):
            parts += [""] * (len(columns) - len(parts))
        rows.append(dict(zip(columns, parts)))
    return rows


def _sql_literal(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def _format_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2)


def _numeric_suffix(value, prefix):
    match = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", str(value or ""))
    if not match:
        raise ToolExecutionError(f"[错误] 非法 {prefix} ID: {value}")
    return int(match.group(1))


def _patient_record_by_external_id(patient_id):
    rows = _query_rows(
        """
        SELECT
          CAST(pid AS CHAR),
          pubpid,
          fname,
          lname,
          COALESCE(DATE_FORMAT(DOB, '%Y-%m-%d'), ''),
          COALESCE(sex, ''),
          COALESCE(email, ''),
          COALESCE(phone_home, '')
        FROM patient_data
        WHERE pubpid = {patient_id}
        LIMIT 1;
        """.format(patient_id=_sql_literal(patient_id)),
        ["pid", "pubpid", "fname", "lname", "dob", "sex", "email", "phone_home"],
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "pid": int(row["pid"]),
        "id": row["pubpid"] or f"PT-{row['pid']}",
        "name": " ".join(part for part in [row["fname"], row["lname"]] if part).strip(),
        "dob": row["dob"],
        "sex": row["sex"],
        "email": row["email"],
        "phone_home": row["phone_home"],
    }


def _patient_notes(pid):
    rows = _query_rows(
        """
        SELECT
          CAST(id AS CHAR),
          COALESCE(user, ''),
          COALESCE(title, ''),
          COALESCE(body, ''),
          COALESCE(DATE_FORMAT(date, '%Y-%m-%d %H:%i:%s'), '')
        FROM pnotes
        WHERE pid = {pid} AND COALESCE(deleted, 0) = 0
        ORDER BY id ASC;
        """.format(pid=int(pid)),
        ["id", "author", "title", "body", "date"],
    )
    return [
        {
            "id": int(row["id"]),
            "author": row["author"],
            "title": row["title"],
            "body": row["body"],
            "date": row["date"],
        }
        for row in rows
    ]


def _appointment_record_by_external_id(appointment_id):
    appointment_num = _numeric_suffix(appointment_id, "APT")
    rows = _query_rows(
        """
        SELECT
          CAST(e.pc_eid AS CHAR),
          COALESCE(p.pubpid, CONCAT('PT-', e.pc_pid)),
          COALESCE(e.pc_eventDate, ''),
          COALESCE(TIME_FORMAT(e.pc_startTime, '%H:%i'), ''),
          COALESCE(TIME_FORMAT(e.pc_endTime, '%H:%i'), ''),
          COALESCE(e.pc_apptstatus, ''),
          COALESCE(e.pc_hometext, ''),
          COALESCE(e.pc_title, '')
        FROM openemr_postcalendar_events e
        LEFT JOIN patient_data p ON p.pid = CAST(e.pc_pid AS UNSIGNED)
        WHERE e.pc_eid = {appointment_num}
        LIMIT 1;
        """.format(appointment_num=appointment_num),
        ["pc_eid", "patient_id", "date", "time", "end_time", "status", "provider", "reason"],
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "id": f"APT-{row['pc_eid']}",
        "appointment_id": f"APT-{row['pc_eid']}",
        "patient_id": row["patient_id"],
        "date": row["date"],
        "time": row["time"],
        "end_time": row["end_time"],
        "status": row["status"],
        "provider": row["provider"],
        "reason": row["reason"],
    }


def _require_patient(patient_id):
    patient = _patient_record_by_external_id(patient_id)
    if not patient:
        raise ToolExecutionError(f"[错误] 找不到患者: {patient_id}")
    return patient


def _require_appointment(appointment_id):
    appointment = _appointment_record_by_external_id(appointment_id)
    if not appointment:
        raise ToolExecutionError(f"[错误] 找不到预约: {appointment_id}")
    return appointment


@openemr_tool(
    "list_patients",
    "列出患者，可按姓名关键词筛选。",
    {
        "name_query": {"type": "string", "description": "姓名关键词"},
    },
)
def list_patients(name_query=""):
    results = []
    rows = _query_rows(
        """
        SELECT
          pubpid,
          fname,
          lname,
          COALESCE(DATE_FORMAT(DOB, '%Y-%m-%d'), ''),
          CAST(pid AS CHAR)
        FROM patient_data
        ORDER BY pid ASC;
        """,
        ["pubpid", "fname", "lname", "dob", "pid"],
    )
    for row in rows:
        name = " ".join(part for part in [row["fname"], row["lname"]] if part).strip()
        if name_query and name_query.lower() not in name.lower():
            continue
        results.append(
            {
                "id": row["pubpid"] or f"PT-{row['pid']}",
                "name": name,
                "dob": row["dob"],
                "note_count": len(_patient_notes(int(row["pid"]))),
            }
        )
    return _format_json(results)


@openemr_tool(
    "get_patient",
    "获取患者档案详情。",
    {
        "patient_id": {"type": "string", "description": "患者 ID"},
    },
)
def get_patient(patient_id):
    patient = _require_patient(patient_id)
    patient["notes"] = _patient_notes(patient["pid"])
    patient["note_count"] = len(patient["notes"])
    return _format_json(patient)


@openemr_tool(
    "list_appointments",
    "列出预约，可按日期、患者或状态筛选。",
    {
        "date": {"type": "string", "description": "日期，如 2026-03-28"},
        "patient_id": {"type": "string", "description": "患者 ID"},
        "status": {"type": "string", "description": "状态，如 scheduled、cancelled"},
    },
)
def list_appointments(date="", patient_id="", status=""):
    results = []
    rows = _query_rows(
        """
        SELECT
          CAST(e.pc_eid AS CHAR),
          COALESCE(p.pubpid, CONCAT('PT-', e.pc_pid)),
          COALESCE(e.pc_eventDate, ''),
          COALESCE(TIME_FORMAT(e.pc_startTime, '%H:%i'), ''),
          COALESCE(e.pc_apptstatus, ''),
          COALESCE(e.pc_hometext, ''),
          COALESCE(e.pc_title, '')
        FROM openemr_postcalendar_events e
        LEFT JOIN patient_data p ON p.pid = CAST(e.pc_pid AS UNSIGNED)
        ORDER BY e.pc_eventDate ASC, e.pc_startTime ASC, e.pc_eid ASC;
        """,
        ["pc_eid", "patient_id", "date", "time", "status", "provider", "reason"],
    )
    for row in rows:
        appointment = {
            "id": f"APT-{row['pc_eid']}",
            "patient_id": row["patient_id"],
            "date": row["date"],
            "time": row["time"],
            "status": row["status"],
            "provider": row["provider"],
            "reason": row["reason"],
        }
        if date and appointment.get("date") != date:
            continue
        if patient_id and appointment.get("patient_id") != patient_id:
            continue
        if status and str(appointment.get("status", "")).lower() != status.lower():
            continue
        results.append(appointment)
    return _format_json(results)


@openemr_tool(
    "add_patient_note",
    "为患者档案追加一条备注。",
    {
        "patient_id": {"type": "string", "description": "患者 ID"},
        "note": {"type": "string", "description": "备注内容"},
        "author": {"type": "string", "description": "备注作者"},
    },
    required=["patient_id", "note"],
    is_write=True,
)
def add_patient_note(patient_id, note, author="nurse"):
    patient = _require_patient(patient_id)
    _run_mysql(
        """
        INSERT INTO pnotes (date, body, pid, user, groupname, activity, authorized, title, assigned_to, deleted, message_status, is_msg_encrypted)
        VALUES (NOW(), {body}, {pid}, {author}, 'Default', 1, 1, 'Pipeline note', '', 0, 'New', 0);
        """.format(
            body=_sql_literal(note),
            pid=patient["pid"],
            author=_sql_literal(author),
        )
    )
    notes = _patient_notes(patient["pid"])
    entry = notes[-1]
    return _format_json(entry)


@openemr_tool(
    "reschedule_appointment",
    "改期预约。",
    {
        "appointment_id": {"type": "string", "description": "预约 ID"},
        "new_date": {"type": "string", "description": "新的日期"},
        "new_time": {"type": "string", "description": "新的时间"},
    },
    required=["appointment_id", "new_date", "new_time"],
    is_write=True,
)
def reschedule_appointment(appointment_id, new_date, new_time):
    appointment = _require_appointment(appointment_id)
    start_dt = datetime.strptime(f"{new_date} {new_time}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=30)
    appointment_num = _numeric_suffix(appointment_id, "APT")
    _run_mysql(
        """
        UPDATE openemr_postcalendar_events
        SET
          pc_eventDate = {new_date},
          pc_endDate = {new_date},
          pc_startTime = {new_start},
          pc_endTime = {new_end}
        WHERE pc_eid = {appointment_num}
        LIMIT 1;
        """.format(
            new_date=_sql_literal(new_date),
            new_start=_sql_literal(start_dt.strftime("%H:%M:%S")),
            new_end=_sql_literal(end_dt.strftime("%H:%M:%S")),
            appointment_num=appointment_num,
        )
    )
    return _format_json({"appointment_id": appointment_id, "date": new_date, "time": new_time})


@openemr_tool(
    "delete_patient",
    "删除患者档案。",
    {
        "patient_id": {"type": "string", "description": "患者 ID"},
    },
    is_write=True,
)
def delete_patient(patient_id):
    patient = _require_patient(patient_id)
    _run_mysql(
        """
        DELETE FROM pnotes WHERE pid = {pid};
        DELETE FROM openemr_postcalendar_events WHERE pc_pid = {pid_text};
        DELETE FROM patient_data WHERE pid = {pid};
        """.format(
            pid=patient["pid"],
            pid_text=_sql_literal(str(patient["pid"])),
        )
    )
    return _format_json({"deleted_patient_id": patient_id})


# ---------------------------------------------------------------------------
# New tools based on OpenEMR REST API
# ---------------------------------------------------------------------------

@openemr_tool(
    "get_appointment",
    "获取单个预约的详细信息。",
    {
        "appointment_id": {"type": "string", "description": "预约 ID，如 APT-100"},
    },
    required=["appointment_id"],
)
def get_appointment(appointment_id):
    appointment = _require_appointment(appointment_id)
    return _format_json(appointment)


@openemr_tool(
    "list_encounters",
    "列出患者的就诊记录。",
    {
        "patient_id": {"type": "string", "description": "患者 ID"},
    },
    required=["patient_id"],
)
def list_encounters(patient_id):
    patient = _require_patient(patient_id)
    rows = _query_rows(
        """
        SELECT
          CAST(id AS CHAR),
          COALESCE(DATE_FORMAT(date, '%Y-%m-%d'), ''),
          COALESCE(reason, ''),
          COALESCE(facility, ''),
          COALESCE(pc_catid, ''),
          COALESCE(provider_id, '')
        FROM form_encounter
        WHERE pid = {pid}
        ORDER BY date DESC;
        """.format(pid=patient["pid"]),
        ["id", "date", "reason", "facility", "category", "provider_id"],
    )
    return _format_json([
        {
            "id": row["id"],
            "patient_id": patient_id,
            "date": row["date"],
            "reason": row["reason"],
            "facility": row["facility"],
        }
        for row in rows
    ])


@openemr_tool(
    "list_patient_allergies",
    "列出患者的过敏记录。",
    {
        "patient_id": {"type": "string", "description": "患者 ID"},
    },
    required=["patient_id"],
)
def list_patient_allergies(patient_id):
    patient = _require_patient(patient_id)
    rows = _query_rows(
        """
        SELECT
          CAST(id AS CHAR),
          COALESCE(title, ''),
          COALESCE(reaction, ''),
          COALESCE(severity_al, ''),
          COALESCE(DATE_FORMAT(begdate, '%Y-%m-%d'), ''),
          COALESCE(activity, 1)
        FROM lists
        WHERE pid = {pid} AND type = 'allergy'
        ORDER BY begdate DESC;
        """.format(pid=patient["pid"]),
        ["id", "title", "reaction", "severity", "begin_date", "active"],
    )
    return _format_json([
        {
            "id": row["id"],
            "patient_id": patient_id,
            "allergen": row["title"],
            "reaction": row["reaction"],
            "severity": row["severity"],
            "begin_date": row["begin_date"],
            "active": row["active"] != "0",
        }
        for row in rows
    ])


@openemr_tool(
    "create_appointment",
    "为患者创建新预约。",
    {
        "patient_id": {"type": "string", "description": "患者 ID"},
        "date": {"type": "string", "description": "日期，如 2026-04-01"},
        "time": {"type": "string", "description": "时间，如 09:30"},
        "reason": {"type": "string", "description": "预约原因"},
        "provider": {"type": "string", "description": "医生"},
    },
    required=["patient_id", "date", "time"],
    is_write=True,
)
def create_appointment(patient_id, date, time, reason="", provider=""):
    patient = _require_patient(patient_id)
    start_dt = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=30)
    _run_mysql(
        """
        INSERT INTO openemr_postcalendar_events
          (pc_catid, pc_multiple, pc_aid, pc_pid, pc_topic, pc_eventDate, pc_endDate, pc_duration,
           pc_recurrtype, pc_recurrfreq, pc_startTime, pc_endTime, pc_alldayevent, pc_eventstatus,
           pc_sharing, pc_apptstatus, pc_prefcatid, pc_facility, pc_sendalertsms, pc_sendalertemail,
           pc_billing_location, pc_room, pc_title, pc_hometext)
        VALUES
          (9, 0, 'admin', {pid}, 1, {date}, {date}, 1800, 0, 0,
           {start_time}, {end_time}, 0, 0, 0, 'scheduled', 0, 0, 'NO', 'NO', 0, '',
           {reason}, {provider});
        """.format(
            pid=_sql_literal(str(patient["pid"])),
            date=_sql_literal(date),
            start_time=_sql_literal(start_dt.strftime("%H:%M:%S")),
            end_time=_sql_literal(end_dt.strftime("%H:%M:%S")),
            reason=_sql_literal(reason),
            provider=_sql_literal(provider),
        )
    )
    new_id = _run_mysql("SELECT LAST_INSERT_ID();", expect_rows=True).strip()
    return _format_json({"appointment_id": f"APT-{new_id}", "patient_id": patient_id, "date": date, "time": time})


@openemr_tool(
    "update_patient",
    "更新患者的基本信息。",
    {
        "patient_id": {"type": "string", "description": "患者 ID"},
        "phone": {"type": "string", "description": "新电话号码"},
        "email": {"type": "string", "description": "新邮箱"},
        "sex": {"type": "string", "description": "性别，如 Male、Female"},
    },
    required=["patient_id"],
    is_write=True,
)
def update_patient(patient_id, phone="", email="", sex=""):
    patient = _require_patient(patient_id)
    updates = []
    if phone:
        updates.append(f"phone_home = {_sql_literal(phone)}")
    if email:
        updates.append(f"email = {_sql_literal(email)}")
    if sex:
        updates.append(f"sex = {_sql_literal(sex)}")
    if not updates:
        raise ToolExecutionError("[错误] 至少需要提供一个要更新的字段")
    _run_mysql(
        "UPDATE patient_data SET {updates} WHERE pid = {pid} LIMIT 1;".format(
            updates=", ".join(updates),
            pid=patient["pid"],
        )
    )
    updated = _patient_record_by_external_id(patient_id)
    return _format_json(updated)


@openemr_tool(
    "add_allergy",
    "为患者添加过敏记录。",
    {
        "patient_id": {"type": "string", "description": "患者 ID"},
        "allergen": {"type": "string", "description": "过敏原名称"},
        "reaction": {"type": "string", "description": "过敏反应"},
        "severity": {"type": "string", "description": "严重程度，如 mild、moderate、severe"},
    },
    required=["patient_id", "allergen"],
    is_write=True,
)
def add_allergy(patient_id, allergen, reaction="", severity=""):
    patient = _require_patient(patient_id)
    _run_mysql(
        """
        INSERT INTO lists (date, type, title, reaction, severity_al, pid, activity, begdate, user, groupname)
        VALUES (NOW(), 'allergy', {allergen}, {reaction}, {severity}, {pid}, 1, CURDATE(), 'admin', 'Default');
        """.format(
            allergen=_sql_literal(allergen),
            reaction=_sql_literal(reaction),
            severity=_sql_literal(severity),
            pid=patient["pid"],
        )
    )
    return _format_json({"patient_id": patient_id, "allergen": allergen, "reaction": reaction, "severity": severity})
