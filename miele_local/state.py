"""Appliance state tracking, summary decoding, and SQLite history persistence."""

import datetime
import json
import logging
import sqlite3
import time
from typing import Any, Dict, List, Optional

from .database import ensure_schema_and_migrate
from .enums import (
    DeviceType,
    DryingStep,
    ProgramPhase,
    RemoteControl,
    Status,
    decode_enum,
    decode_program_id,
    get_device_category,
)

logger = logging.getLogger(__name__)


def _tuple_to_minutes(value: Any) -> Optional[int]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0]) * 60 + int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _decode_temperatures(raw_list: Any) -> List[Optional[float]]:
    """Convert Miele centi-degree array (e.g. [4000, -32768, -32768]) to Celsius floats."""
    if not isinstance(raw_list, list):
        return []
    result: List[Optional[float]] = []
    for val in raw_list:
        try:
            num = int(val)
            if num <= -27315 or num == -32768:
                result.append(None)
            else:
                result.append(round(num / 100.0, 2))
        except (TypeError, ValueError):
            result.append(None)
    return result


def build_annotated_summary(
    raw_state: Dict[str, Any],
    raw_ident: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Combine Miele /State and /Ident payloads and add decoded human-friendly fields."""
    merged: Dict[str, Any] = dict(raw_state or {})
    if raw_ident:
        merged.update(raw_ident)

    device_type = merged.get("DeviceType")
    merged["DecodedDeviceType"] = decode_enum(DeviceType, device_type)
    merged["DeviceCategory"] = get_device_category(device_type)
    merged["DecodedStatus"] = decode_enum(Status, merged.get("Status"))
    merged["DecodedProgramPhase"] = decode_enum(ProgramPhase, merged.get("ProgramPhase"))
    merged["DecodedProgramID"] = decode_program_id(merged.get("ProgramID"), device_type)
    if "DryingStep" in merged and merged["DryingStep"] is not None:
        merged["DecodedDryingStep"] = decode_enum(DryingStep, merged.get("DryingStep"))

    elapsed = _tuple_to_minutes(merged.get("ElapsedTime"))
    remaining = _tuple_to_minutes(merged.get("RemainingTime"))
    start_delay = _tuple_to_minutes(merged.get("StartTime"))

    if elapsed is not None and remaining is not None:
        total = elapsed + remaining
        progress = 0.0 if total < 1 else round(elapsed / total, 4)
        merged["RemainingMinutes"] = remaining
        merged["ElapsedMinutes"] = elapsed
        merged["Progress"] = str(progress)
        merged["ProgressPercent"] = round(progress * 100.0, 1)
    else:
        merged["RemainingMinutes"] = -1
        merged["ElapsedMinutes"] = -1
        merged["Progress"] = -1
        merged["ProgressPercent"] = 0.0

    merged["StartDelayMinutes"] = start_delay if start_delay is not None else 0

    temps_c = _decode_temperatures(merged.get("Temperature"))
    target_temps_c = _decode_temperatures(merged.get("TargetTemperature"))
    merged["TemperatureCelsius"] = temps_c
    merged["TargetTemperatureCelsius"] = target_temps_c
    merged["CurrentTemperature"] = next((t for t in temps_c if t is not None), None)
    merged["TargetTemperatureValue"] = next((t for t in target_temps_c if t is not None), None)

    remote_enable = merged.get("RemoteEnable")
    if isinstance(remote_enable, list) and len(remote_enable) >= 1:
        re_code = remote_enable[0]
        merged["DecodedRemoteControl"] = decode_enum(RemoteControl, re_code)
        re_capable = (re_code == RemoteControl.Full.value) or (
            len(remote_enable) >= 3 and bool(remote_enable[2])
        )
    else:
        merged["DecodedRemoteControl"] = "Unknown"
        re_capable = False

    status_val = merged.get("Status")
    prog_type = merged.get("ProgramType")
    prog_id = merged.get("ProgramID") or 0
    # Issue #27 fix: ovens & washers with an armed timer have Status=WaitingToStart(4)/Programmed(3)
    # even when ProgramType is BuiltInFunction(1), and RemoteEnable[0]==15 (Full).
    re_ready = bool(
        re_capable
        and (
            status_val in (Status.Programmed.value, Status.WaitingToStart.value)
            or (prog_type == 2 and prog_id != 0)
        )
    )
    merged["RemoteStartCapable"] = bool(re_capable)
    merged["RemoteStartReady"] = re_ready
    return merged


class ApplianceStateManager:
    """Manages persistent appliance records, live state cache, and SQLite history."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        ensure_schema_and_migrate(self.db_path)
        self.appliances: Dict[int, Dict[str, Any]] = {}
        self.endpoint_to_id: Dict[str, int] = {}
        self.current_state: Dict[int, Dict[str, Any]] = {}
        self.last_saved_bucket: Dict[int, str] = {}
        self.last_state_fingerprint: Dict[int, str] = {}
        self.reload_appliances()
        self._load_latest_history()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def reload_appliances(self) -> None:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM appliances ORDER BY device_id").fetchall()
            self.appliances = {}
            self.endpoint_to_id = {}
            for row in rows:
                item = dict(row)
                did = item["device_id"]
                self.appliances[did] = item
                self.endpoint_to_id[item["endpoint_name"]] = did
        finally:
            conn.close()

    def _load_latest_history(self) -> None:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT device_id, timestamp_bucket, raw_json
                FROM appliance_state_history
                WHERE (device_id, timestamp_bucket) IN (
                    SELECT device_id, MAX(timestamp_bucket)
                    FROM appliance_state_history
                    GROUP BY device_id
                )
                """
            ).fetchall()
            for row in rows:
                did = row["device_id"]
                self.last_saved_bucket[did] = row["timestamp_bucket"]
                if row["raw_json"]:
                    try:
                        parsed = json.loads(row["raw_json"])
                        self.current_state[did] = parsed
                    except Exception:
                        pass
        finally:
            conn.close()

    def get_all_appliances(self) -> List[Dict[str, Any]]:
        return list(self.appliances.values())

    def get_appliance(self, identifier: Any) -> Optional[Dict[str, Any]]:
        """Resolve appliance by numeric device_id or string endpoint_name."""
        if isinstance(identifier, int):
            return self.appliances.get(identifier)
        if isinstance(identifier, str):
            if identifier.isdigit():
                did = int(identifier)
                if did in self.appliances:
                    return self.appliances[did]
            did = self.endpoint_to_id.get(identifier)
            if did is not None:
                return self.appliances.get(did)
        return None

    def upsert_appliance(
        self,
        endpoint_name: str,
        display_name: str,
        host: str,
        group_id: str,
        group_key: str,
        device_route: str = "auto",
        poll_interval: int = 15,
    ) -> int:
        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT device_id FROM appliances WHERE endpoint_name = ?",
                (endpoint_name,),
            ).fetchone()
            if existing:
                did = existing["device_id"]
                conn.execute(
                    """
                    UPDATE appliances
                    SET display_name = ?, host = ?, group_id = ?, group_key = ?,
                        device_route = ?, poll_interval = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE device_id = ?
                    """,
                    (display_name, host, group_id, group_key, device_route, poll_interval, did),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO appliances
                    (endpoint_name, display_name, host, group_id, group_key, device_route, poll_interval)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (endpoint_name, display_name, host, group_id, group_key, device_route, poll_interval),
                )
                did = int(cur.lastrowid)
            conn.commit()
        finally:
            conn.close()
        self.reload_appliances()
        return did

    def update_appliance_metadata(self, device_id: int, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "display_name",
            "host",
            "device_route",
            "device_type",
            "decoded_device_type",
            "category",
            "tech_type",
            "serial_number",
            "xkm_tech_type",
            "xkm_release_version",
            "dop2_supported",
            "remote_start_capable",
            "enabled",
            "poll_interval",
            "last_seen",
            "last_error",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates) + ", updated_at = CURRENT_TIMESTAMP"
        values = list(updates.values()) + [device_id]
        conn = self._connect()
        try:
            conn.execute(f"UPDATE appliances SET {set_clause} WHERE device_id = ?", values)
            conn.commit()
        finally:
            conn.close()
        self.reload_appliances()

    def delete_appliance(self, device_id: int) -> bool:
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM appliances WHERE device_id = ?", (device_id,))
            conn.commit()
            deleted = cur.rowcount > 0
        finally:
            conn.close()
        self.current_state.pop(device_id, None)
        self.last_saved_bucket.pop(device_id, None)
        self.last_state_fingerprint.pop(device_id, None)
        self.reload_appliances()
        return deleted

    def record_state(self, device_id: int, summary: Dict[str, Any], timestamp: Optional[float] = None) -> bool:
        """Update in-memory state and persist to history when values or time bucket change.

        Returns True if the meaningful state changed compared to the previous reading.
        """
        ts = timestamp or time.time()
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        bucket = dt.strftime("%Y%m%d%H%M") + f"{(dt.second // 10) * 10:02d}"

        self.current_state[device_id] = summary
        fingerprint_fields = {
            "Status": summary.get("Status"),
            "ProgramID": summary.get("ProgramID"),
            "ProgramPhase": summary.get("ProgramPhase"),
            "RemainingMinutes": summary.get("RemainingMinutes"),
            "CurrentTemperature": summary.get("CurrentTemperature"),
            "TargetTemperatureValue": summary.get("TargetTemperatureValue"),
            "SignalDoor": summary.get("SignalDoor"),
            "SignalInfo": summary.get("SignalInfo"),
            "SignalFailure": summary.get("SignalFailure"),
            "Light": summary.get("Light"),
            "StandbyState": summary.get("StandbyState"),
        }
        fingerprint = json.dumps(fingerprint_fields, sort_keys=True)
        changed = self.last_state_fingerprint.get(device_id) != fingerprint
        self.last_state_fingerprint[device_id] = fingerprint

        if changed or self.last_saved_bucket.get(device_id) != bucket:
            self.last_saved_bucket[device_id] = bucket
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO appliance_state_history (
                        device_id, timestamp_bucket, status_code, decoded_status,
                        program_id, decoded_program_id, program_phase, decoded_program_phase,
                        progress, remaining_minutes, elapsed_minutes,
                        current_temperature, target_temperature, spinning_speed,
                        signal_door, signal_info, signal_failure, light, standby_state,
                        raw_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        device_id,
                        bucket,
                        summary.get("Status"),
                        summary.get("DecodedStatus"),
                        summary.get("ProgramID"),
                        summary.get("DecodedProgramID"),
                        summary.get("ProgramPhase"),
                        summary.get("DecodedProgramPhase"),
                        summary.get("ProgressPercent"),
                        summary.get("RemainingMinutes"),
                        summary.get("ElapsedMinutes"),
                        summary.get("CurrentTemperature"),
                        summary.get("TargetTemperatureValue"),
                        summary.get("SpinningSpeed"),
                        int(bool(summary.get("SignalDoor"))),
                        int(bool(summary.get("SignalInfo"))),
                        int(bool(summary.get("SignalFailure"))),
                        summary.get("Light"),
                        summary.get("StandbyState"),
                        json.dumps(summary),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        self._track_run_lifecycle(device_id, summary, dt)
        return changed

    def _track_run_lifecycle(
        self,
        device_id: int,
        summary: Dict[str, Any],
        now_dt: datetime.datetime,
    ) -> None:
        """Automatically log start, progress, and completion of appliance cycles."""
        status_code = summary.get("Status")
        if status_code is None:
            return

        active_statuses = {
            Status.Running.value,
            Status.Rinse.value,
            Status.SuperFreeze.value,
            Status.SuperCool.value,
            Status.SuperHeat.value,
        }
        paused_statuses = {Status.Paused.value}

        elapsed_min = summary.get("ElapsedMinutes")
        elapsed_val = int(elapsed_min) if isinstance(elapsed_min, (int, float)) and elapsed_min >= 0 else 0
        program_id = summary.get("ProgramID")
        decoded_program = summary.get("DecodedProgramID")
        target_temp = summary.get("TargetTemperatureValue")
        spin_speed = summary.get("SpinningSpeed")
        now_iso = now_dt.isoformat()

        conn = self._connect()
        try:
            open_run = conn.execute(
                """
                SELECT * FROM appliance_runs
                WHERE device_id = ? AND outcome = 'in_progress'
                ORDER BY run_id DESC LIMIT 1
                """,
                (device_id,),
            ).fetchone()

            if status_code in active_statuses:
                if open_run is None:
                    start_dt = (
                        now_dt - datetime.timedelta(minutes=elapsed_val)
                        if elapsed_val > 0
                        else now_dt
                    )
                    conn.execute(
                        """
                        INSERT INTO appliance_runs (
                            device_id, started_at, duration_minutes, program_id,
                            decoded_program_id, target_temperature, spinning_speed, outcome
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'in_progress')
                        """,
                        (
                            device_id,
                            start_dt.isoformat(),
                            elapsed_val,
                            program_id,
                            decoded_program,
                            target_temp,
                            spin_speed,
                        ),
                    )
                else:
                    wall_min = self._minutes_between(open_run["started_at"], now_dt)
                    duration = max(int(open_run["duration_minutes"] or 0), elapsed_val, wall_min)
                    conn.execute(
                        """
                        UPDATE appliance_runs
                        SET duration_minutes = ?,
                            program_id = COALESCE(?, program_id),
                            decoded_program_id = COALESCE(?, decoded_program_id),
                            target_temperature = COALESCE(?, target_temperature),
                            spinning_speed = COALESCE(?, spinning_speed),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE run_id = ?
                        """,
                        (
                            duration,
                            program_id if program_id else None,
                            decoded_program if decoded_program not in (None, "NotSelected", "NoProgram") else None,
                            target_temp,
                            spin_speed if spin_speed else None,
                            open_run["run_id"],
                        ),
                    )
                conn.commit()
            elif status_code in paused_statuses and open_run is not None:
                wall_min = self._minutes_between(open_run["started_at"], now_dt)
                duration = max(int(open_run["duration_minutes"] or 0), elapsed_val, wall_min)
                conn.execute(
                    "UPDATE appliance_runs SET duration_minutes = ?, updated_at = CURRENT_TIMESTAMP WHERE run_id = ?",
                    (duration, open_run["run_id"]),
                )
                conn.commit()
            elif open_run is not None:
                wall_min = self._minutes_between(open_run["started_at"], now_dt)
                duration = max(int(open_run["duration_minutes"] or 0), elapsed_val, wall_min)
                if status_code == Status.Failure.value:
                    outcome = "failed"
                elif status_code == Status.Abort.value:
                    outcome = "aborted"
                elif status_code == Status.EndedSuccessfully.value or duration >= 1:
                    outcome = "completed"
                else:
                    outcome = "aborted"

                prog_name = open_run["decoded_program_id"] or decoded_program or "Unknown"
                energy_kwh = open_run["energy_kwh"]
                water_litres = open_run["water_litres"]
                consumption_source = open_run["consumption_source"]

                if energy_kwh is None and water_litres is None and outcome == "completed":
                    est = conn.execute(
                        (
                            "SELECT energy_kwh, water_litres FROM program_estimates "
                            "WHERE device_id = ? AND program_name = ?"
                        ),
                        (device_id, prog_name),
                    ).fetchone()
                    if est:
                        energy_kwh = est["energy_kwh"]
                        water_litres = est["water_litres"]
                        consumption_source = "estimated"

                conn.execute(
                    """
                    UPDATE appliance_runs
                    SET ended_at = ?,
                        duration_minutes = ?,
                        final_status = ?,
                        decoded_final_status = ?,
                        outcome = ?,
                        energy_kwh = ?,
                        water_litres = ?,
                        consumption_source = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE run_id = ?
                    """,
                    (
                        now_iso,
                        duration,
                        status_code,
                        summary.get("DecodedStatus"),
                        outcome,
                        energy_kwh,
                        water_litres,
                        consumption_source,
                        open_run["run_id"],
                    ),
                )
                conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _minutes_between(start_iso: str, end_dt: datetime.datetime) -> int:
        try:
            start_dt = datetime.datetime.fromisoformat(start_iso)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=datetime.timezone.utc)
            return max(0, int((end_dt - start_dt).total_seconds() // 60))
        except Exception:
            return 0

    def update_run_consumption(
        self,
        device_id: int,
        energy_kwh: Optional[float],
        water_litres: Optional[float],
        source: str = "dop2",
    ) -> None:
        """Attach measured DOP2 consumption to the active or most recent run."""
        if energy_kwh is None and water_litres is None:
            return
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT run_id FROM appliance_runs
                WHERE device_id = ?
                ORDER BY CASE WHEN outcome = 'in_progress' THEN 0 ELSE 1 END, run_id DESC
                LIMIT 1
                """,
                (device_id,),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE appliance_runs
                    SET energy_kwh = COALESCE(?, energy_kwh),
                        water_litres = COALESCE(?, water_litres),
                        consumption_source = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE run_id = ?
                    """,
                    (energy_kwh, water_litres, source, row["run_id"]),
                )
                conn.commit()
        finally:
            conn.close()

    def set_program_estimate(
        self,
        device_id: int,
        program_name: str,
        energy_kwh: Optional[float],
        water_litres: Optional[float],
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO program_estimates
                (device_id, program_name, energy_kwh, water_litres, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (device_id, program_name.strip(), energy_kwh, water_litres),
            )
            conn.commit()
        finally:
            conn.close()

    def get_program_estimates(self, device_id: int) -> Dict[str, Dict[str, Optional[float]]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                (
                    "SELECT program_name, energy_kwh, water_litres "
                    "FROM program_estimates WHERE device_id = ? ORDER BY program_name"
                ),
                (device_id,),
            ).fetchall()
            return {
                row["program_name"]: {
                    "energy_kwh": row["energy_kwh"],
                    "water_litres": row["water_litres"],
                }
                for row in rows
            }
        finally:
            conn.close()

    def delete_run(self, device_id: int, run_id: int) -> bool:
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM appliance_runs WHERE device_id = ? AND run_id = ?",
                (device_id, run_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_statistics(
        self,
        device_id: Optional[int] = None,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Compute run counts, duration, program/temp/spin distributions, and consumption."""
        params: List[Any] = []
        where_clauses = ["outcome != 'in_progress'"]
        if device_id is not None:
            where_clauses.append("r.device_id = ?")
            params.append(device_id)
        if days > 0:
            cutoff = (
                datetime.datetime.now(tz=datetime.timezone.utc)
                - datetime.timedelta(days=days)
            ).isoformat()
            where_clauses.append("r.started_at >= ?")
            params.append(cutoff)

        where_sql = " AND ".join(where_clauses)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT r.*, a.display_name, a.endpoint_name
                FROM appliance_runs r
                JOIN appliances a ON a.device_id = r.device_id
                WHERE {where_sql}
                ORDER BY r.started_at DESC
                """,
                params,
            ).fetchall()

            active_query = (
                "SELECT r.*, a.display_name, a.endpoint_name "
                "FROM appliance_runs r JOIN appliances a ON a.device_id = r.device_id "
                "WHERE outcome = 'in_progress'"
            )
            active_params: List[Any] = []
            if device_id is not None:
                active_query += " AND r.device_id = ?"
                active_params.append(device_id)
            active_rows = [dict(r) for r in conn.execute(active_query, active_params).fetchall()]
        finally:
            conn.close()

        total_runs = len(rows)
        completed_runs = sum(1 for r in rows if r["outcome"] == "completed")
        aborted_runs = sum(1 for r in rows if r["outcome"] == "aborted")
        failed_runs = sum(1 for r in rows if r["outcome"] == "failed")
        total_minutes = sum(int(r["duration_minutes"] or 0) for r in rows)
        avg_minutes = round(total_minutes / total_runs, 1) if total_runs else 0.0
        total_energy = round(sum(float(r["energy_kwh"] or 0.0) for r in rows), 2)
        total_water = round(sum(float(r["water_litres"] or 0.0) for r in rows), 1)

        prog_map: Dict[str, Dict[str, Any]] = {}
        temp_map: Dict[str, int] = {}
        spin_map: Dict[int, int] = {}
        by_hour = [0] * 24
        weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        by_weekday = {d: 0 for d in weekday_names}

        for r in rows:
            prog = r["decoded_program_id"] or "Unknown"
            entry = prog_map.setdefault(prog, {"program": prog, "count": 0, "total_minutes": 0})
            entry["count"] += 1
            entry["total_minutes"] += int(r["duration_minutes"] or 0)

            if r["target_temperature"] is not None:
                t_key = f"{r['target_temperature']:g}°C"
                temp_map[t_key] = temp_map.get(t_key, 0) + 1
            if r["spinning_speed"] is not None and int(r["spinning_speed"]) > 0:
                rpm = int(r["spinning_speed"])
                spin_map[rpm] = spin_map.get(rpm, 0) + 1

            try:
                dt = datetime.datetime.fromisoformat(r["started_at"])
                by_hour[dt.hour] += 1
                by_weekday[weekday_names[dt.weekday()]] += 1
            except Exception:
                pass

        by_program = sorted(prog_map.values(), key=lambda x: (-x["count"], x["program"]))
        for item in by_program:
            item["share_percent"] = round((item["count"] / total_runs) * 100.0, 1) if total_runs else 0.0

        by_temperature = [{"temperature": k, "count": v} for k, v in sorted(temp_map.items())]
        by_spin_speed = [{"rpm": k, "count": v} for k, v in sorted(spin_map.items(), reverse=True)]

        recent_runs = [
            {
                "run_id": r["run_id"],
                "device_id": r["device_id"],
                "device_name": r["display_name"],
                "endpoint": r["endpoint_name"],
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "duration_minutes": r["duration_minutes"],
                "program": r["decoded_program_id"] or "Unknown",
                "target_temperature": r["target_temperature"],
                "spinning_speed": r["spinning_speed"],
                "outcome": r["outcome"],
                "energy_kwh": r["energy_kwh"],
                "water_litres": r["water_litres"],
                "consumption_source": r["consumption_source"],
            }
            for r in rows[:30]
        ]

        return {
            "device_id": device_id,
            "days": days,
            "total_runs": total_runs,
            "completed_runs": completed_runs,
            "aborted_runs": aborted_runs,
            "failed_runs": failed_runs,
            "total_duration_minutes": total_minutes,
            "avg_duration_minutes": avg_minutes,
            "total_energy_kwh": total_energy,
            "total_water_litres": total_water,
            "active_runs": active_rows,
            "by_program": by_program,
            "by_temperature": by_temperature,
            "by_spin_speed": by_spin_speed,
            "by_hour": by_hour,
            "by_weekday": [{"day": d, "count": by_weekday[d]} for d in weekday_names],
            "recent_runs": recent_runs,
            "program_estimates": self.get_program_estimates(device_id) if device_id is not None else {},
        }

    def get_history(
        self,
        device_id: int,
        hours: int = 24,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        cutoff_dt = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(hours=hours)
        cutoff_bucket = cutoff_dt.strftime("%Y%m%d%H%M%S")
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT timestamp_bucket, status_code, decoded_status, program_id, decoded_program_id,
                       program_phase, decoded_program_phase, progress, remaining_minutes, elapsed_minutes,
                       current_temperature, target_temperature, spinning_speed,
                       signal_door, signal_info, signal_failure
                FROM appliance_state_history
                WHERE device_id = ? AND timestamp_bucket >= ?
                ORDER BY timestamp_bucket DESC
                LIMIT ?
                """,
                (device_id, cutoff_bucket, limit),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for row in rows:
                b = row["timestamp_bucket"]
                iso_ts = (
                    f"{b[0:4]}-{b[4:6]}-{b[6:8]}T{b[8:10]}:{b[10:12]}:{b[12:14]}Z"
                    if len(b) == 14
                    else b
                )
                out.append(
                    {
                        "timestamp": iso_ts,
                        "bucket": b,
                        "status": row["status_code"],
                        "decoded_status": row["decoded_status"],
                        "program_id": row["program_id"],
                        "decoded_program_id": row["decoded_program_id"],
                        "program_phase": row["program_phase"],
                        "decoded_program_phase": row["decoded_program_phase"],
                        "progress_percent": row["progress"],
                        "remaining_minutes": row["remaining_minutes"],
                        "elapsed_minutes": row["elapsed_minutes"],
                        "current_temperature": row["current_temperature"],
                        "target_temperature": row["target_temperature"],
                        "spinning_speed": row["spinning_speed"],
                        "signal_door": bool(row["signal_door"]),
                        "signal_info": bool(row["signal_info"]),
                        "signal_failure": bool(row["signal_failure"]),
                    }
                )
            return out
        finally:
            conn.close()

    def purge_old_history(self, days: int) -> int:
        if days < 1:
            return 0
        cutoff_dt = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=days)
        cutoff_bucket = cutoff_dt.strftime("%Y%m%d%H%M%S")
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM appliance_state_history WHERE timestamp_bucket < ?",
                (cutoff_bucket,),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def save_dop2_cache(self, device_id: int, tree_data: Dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO dop2_cache (device_id, dop2_tree_json, fetched_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (device_id, json.dumps(tree_data)),
            )
            conn.commit()
        finally:
            conn.close()

    def get_dop2_cache(self, device_id: int) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT dop2_tree_json, fetched_at FROM dop2_cache WHERE device_id = ?",
                (device_id,),
            ).fetchone()
            if not row:
                return None
            data = json.loads(row["dop2_tree_json"])
            data["cached_at"] = row["fetched_at"]
            return data
        finally:
            conn.close()

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default
        finally:
            conn.close()

    def set_setting(self, key: str, value: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO app_settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()
