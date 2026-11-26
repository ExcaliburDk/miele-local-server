"""Tests for device-type-aware enum decoding and summary generation (Issues #7, #11, #27)."""

from miele_local.enums import DeviceType, decode_program_id
from miele_local.state import build_annotated_summary


def test_program_id_disambiguation_oven_vs_washer():
    # Issue #27: Oven ProgramID 24 is TopBottomHeat, not a laundry collision
    assert decode_program_id(24, DeviceType.Oven.value) == "TopBottomHeat"
    assert decode_program_id(13, DeviceType.Oven.value) == "HotAirPlus"
    assert decode_program_id(190, DeviceType.WashingMachine.value) == "EuropeanUnionEcoMode"
    assert decode_program_id(178, DeviceType.WashingMachine.value) == "HandWash"
    assert decode_program_id(21, DeviceType.WashingMachine.value) == "DrainSpin"


def test_oven_summary_decodes_temperatures_and_remote_start():
    # Payload modelled directly from Issue #27 (Oven H7264BP)
    raw_state = {
        "Status": 4,
        "InternalState": 0,
        "ProgramType": 1,
        "ProgramID": 24,
        "ProgramPhase": 0,
        "RemainingTime": [0, 6],
        "StartTime": [0, 25],
        "TargetTemperature": [4000, -32768, -32768],
        "Temperature": [3648, -32768, -32768],
        "SignalInfo": False,
        "SignalFailure": False,
        "SignalDoor": False,
        "RemoteEnable": [15, 0, 1],
        "Light": 2,
        "StandbyState": 1,
        "ElapsedTime": [0, 0],
    }
    raw_ident = {
        "DeviceType": 12,
        "DeviceIdentLabel": {"TechType": "H7264BP", "FabNumber": "000111222333"},
        "XKMIdentLabel": {"TechType": "EK037", "ReleaseVersion": "03.97"},
    }
    summary = build_annotated_summary(raw_state, raw_ident)
    assert summary["DecodedDeviceType"] == "Oven"
    assert summary["DeviceCategory"] == "oven"
    assert summary["DecodedProgramID"] == "TopBottomHeat"
    assert summary["DecodedStatus"] == "WaitingToStart"
    assert summary["CurrentTemperature"] == 36.48
    assert summary["TargetTemperatureValue"] == 40.0
    assert summary["TemperatureCelsius"] == [36.48, None, None]
    assert summary["StartDelayMinutes"] == 25
    assert summary["RemoteStartCapable"] is True
    assert summary["RemoteStartReady"] is True
