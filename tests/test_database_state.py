"""Tests for SQLite database schema, appliance persistence, and state history."""

import time

from miele_local.state import ApplianceStateManager, build_annotated_summary


def test_state_manager_crud_and_history(tmp_path):
    db_file = str(tmp_path / "miele.db")
    mgr = ApplianceStateManager(db_file)

    did = mgr.upsert_appliance(
        endpoint_name="washer",
        display_name="Laundry Washer",
        host="192.168.1.50",
        group_id="8E156D44B8587D36",
        group_key="AA" * 64,
        device_route="000151876559",
    )
    assert did > 0
    assert mgr.get_appliance("washer")["host"] == "192.168.1.50"
    assert mgr.get_appliance(str(did))["endpoint_name"] == "washer"

    summary = build_annotated_summary(
        {
            "Status": 5,
            "ProgramID": 190,
            "ProgramPhase": 260,
            "RemainingTime": [1, 30],
            "ElapsedTime": [0, 30],
            "Temperature": [4000, -32768],
            "TargetTemperature": [4000, -32768],
            "SpinningSpeed": 1400,
        },
        {"DeviceType": 1},
    )
    changed = mgr.record_state(did, summary, timestamp=time.time())
    assert changed is True

    history = mgr.get_history(did, hours=24)
    assert len(history) == 1
    assert history[0]["decoded_program_id"] == "EuropeanUnionEcoMode"
    assert history[0]["progress_percent"] == 25.0

    # Settings persistence
    mgr.set_setting("home_name", "Lars Home")
    assert mgr.get_setting("home_name") == "Lars Home"

    # Finish the first EU Eco run cleanly, then log a full Cotton run with estimates
    t0 = time.time()
    mgr.record_state(
        did,
        build_annotated_summary(
            {"Status": 7, "ProgramID": 190, "ProgramPhase": 268, "ElapsedTime": [2, 0], "RemainingTime": [0, 0]},
            {"DeviceType": 1},
        ),
        timestamp=t0 + 60,
    )

    mgr.set_program_estimate(did, "Cotton", energy_kwh=0.95, water_litres=48.0)
    t_cotton = t0 + 120
    running_summary = build_annotated_summary(
        {
            "Status": 5,
            "ProgramID": 9,
            "ProgramPhase": 260,
            "RemainingTime": [1, 0],
            "ElapsedTime": [0, 15],
            "TargetTemperature": [4000, -32768],
            "SpinningSpeed": 1400,
        },
        {"DeviceType": 1},
    )
    mgr.record_state(did, running_summary, timestamp=t_cotton)

    done_summary = build_annotated_summary(
        {
            "Status": 7,
            "ProgramID": 9,
            "ProgramPhase": 268,
            "RemainingTime": [0, 0],
            "ElapsedTime": [1, 15],
            "TargetTemperature": [4000, -32768],
            "SpinningSpeed": 1400,
        },
        {"DeviceType": 1},
    )
    mgr.record_state(did, done_summary, timestamp=t_cotton + 3600)

    stats = mgr.get_statistics(did, days=30)
    assert stats["total_runs"] == 2
    assert stats["completed_runs"] == 2
    assert stats["total_duration_minutes"] == 120 + 75
    assert stats["total_energy_kwh"] == 0.95
    assert stats["total_water_litres"] == 48.0
    assert stats["recent_runs"][0]["program"] == "Cotton"
    assert stats["recent_runs"][0]["consumption_source"] == "estimated"

    # Aliased estimate saved retroactively (ECO 40-60 -> EuropeanUnionEcoMode) backfills earlier run
    mgr.set_program_estimate(did, "ECO 40-60", energy_kwh=0.4, water_litres=35.9)
    stats_after = mgr.get_statistics(did, days=30)
    assert stats_after["total_energy_kwh"] == 1.35
    assert stats_after["total_water_litres"] == 83.9

    assert mgr.delete_appliance(did) is True
    assert mgr.get_appliance(did) is None
