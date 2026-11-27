"""FastAPI integration tests for modern REST, Web UI, Auth, and Legacy endpoints."""

from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from miele_local.api import MieleLocalAPI
from miele_local.crypto import MieleDOP2UnavailableError, MieleLocalError
from miele_local.routes import create_app, register_routes
from miele_local.state import build_annotated_summary


@pytest.fixture
def client_and_api(tmp_path):
    db_path = str(tmp_path / "test_routes.db")
    api = MieleLocalAPI(db_path)
    did = api.state_manager.upsert_appliance(
        endpoint_name="oven",
        display_name="Oven H7264BP",
        host="192.168.1.123",
        group_id="8E156D44B8587D36",
        group_key="AB" * 64,
        device_route="000123456789",
    )
    summary = build_annotated_summary(
        {
            "Status": 4,
            "ProgramType": 1,
            "ProgramID": 24,
            "ProgramPhase": 0,
            "RemainingTime": [0, 6],
            "StartTime": [0, 25],
            "ElapsedTime": [0, 0],
            "TargetTemperature": [4000, -32768, -32768],
            "Temperature": [3648, -32768, -32768],
            "RemoteEnable": [15, 0, 1],
            "SignalDoor": False,
            "SignalInfo": True,
            "SignalFailure": False,
        },
        {"DeviceType": 12, "DeviceIdentLabel": {"TechType": "H7264BP"}},
    )
    api.state_manager.update_appliance_metadata(
        did, device_type=12, decoded_device_type="Oven", category="oven", tech_type="H7264BP"
    )
    api.state_manager.record_state(did, summary)

    app = create_app()
    register_routes(app, lambda: api)
    return TestClient(app), api, did


def test_webui_and_health_routes(client_and_api):
    client, _, _ = client_and_api
    r_root = client.get("/")
    assert r_root.status_code == 200
    assert "Miele Local" in r_root.text

    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "ok"

    r_status = client.get("/status")
    assert r_status.status_code == 200
    assert r_status.json()["configured_devices"] == 1


def test_devices_and_history_routes(client_and_api):
    client, _, did = client_and_api
    r_list = client.get("/devices")
    assert r_list.status_code == 200
    data = r_list.json()
    assert data["count"] == 1
    assert data["devices"][0]["state"]["DecodedProgramID"] == "TopBottomHeat"
    assert data["devices"][0]["state"]["CurrentTemperature"] == 36.48

    r_hist = client.get(f"/devices/{did}/history?hours=24")
    assert r_hist.status_code == 200
    assert r_hist.json()["count"] == 1


def test_legacy_start_get_oven_issue_27(client_and_api):
    client, api, _ = client_and_api
    cached = api.state_manager.current_state[1]
    with patch.object(api, "poll_appliance", new=AsyncMock(return_value=cached)):
        r = client.get("/start/oven")
    assert r.status_code == 200
    body = r.json()
    assert body["DeviceRemoteStartCapable"] is True
    assert body["DeviceRemoteStartEnabled"] is True


def test_walkdop2tree_returns_404_not_500_when_unsupported(client_and_api):
    client, api, _ = client_and_api
    with patch.object(
        api,
        "walk_dop2_tree",
        new=AsyncMock(side_effect=MieleDOP2UnavailableError("DOP2 Root Node not found", "192.168.1.123")),
    ):
        r = client.get("/walkdop2tree/oven")
    assert r.status_code == 404
    assert r.json()["error"] == "DOP2 Root Node not found"


def test_consumables_endpoint_reports_alerts_without_dop2(client_and_api):
    client, api, did = client_and_api
    with patch(
        "miele_local.crypto.MieleCryptoProvider.read_dop2_leaf",
        new=AsyncMock(side_effect=MieleDOP2UnavailableError("not supported", "192.168.1.123")),
    ):
        r = client.get(f"/devices/{did}/consumables")
    assert r.status_code == 200
    body = r.json()
    assert body["signal_info"] is True
    assert body["dop2_available"] is False


def test_home_name_update(client_and_api):
    client, _, _ = client_and_api
    r = client.put("/settings/home-name", json={"name": "My Miele Home"})
    assert r.status_code == 200
    assert r.json()["name"] == "My Miele Home"
    assert client.get("/settings/home-name").json()["name"] == "My Miele Home"


def test_setup_commission_returns_readable_error_on_failure(client_and_api):
    client, _, _ = client_and_api
    mock_result = {
        "success": False,
        "error_title": "Appliance at 10.0.0.185 rejected commissioning (HTTP 403 Forbidden)",
        "steps_to_fix": ["Step 1", "Step 2"],
        "attempts": [{"url": "http://10.0.0.185/Security/Commissioning", "status": 403, "response": ""}],
        "hint": "Device returned HTTP 403 Forbidden.",
    }
    with patch("miele_local.routes.provision_keys_on_device", new=AsyncMock(return_value=mock_result)):
        r = client.post("/setup/commission", json={"host": "10.0.0.185", "endpoint_name": "washer"})
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is False
    assert "403" in data["error_title"]
    assert len(data["steps_to_fix"]) == 2


def test_statistics_and_program_estimates_routes(client_and_api):
    client, _, did = client_and_api
    r_est = client.put(
        f"/devices/{did}/program-estimates",
        json={"program_name": "TopBottomHeat", "energy_kwh": 1.2, "water_litres": 0.0},
    )
    assert r_est.status_code == 200
    assert r_est.json()["saved"] is True

    r_dev_stats = client.get(f"/devices/{did}/statistics?days=30")
    assert r_dev_stats.status_code == 200
    assert r_dev_stats.json()["total_runs"] == 0
    assert r_dev_stats.json()["program_estimates"]["TopBottomHeat"]["energy_kwh"] == 1.2

    r_del_est = client.delete(f"/devices/{did}/program-estimates/TopBottomHeat")
    assert r_del_est.status_code == 200
    assert r_del_est.json()["deleted"] is True
    assert "TopBottomHeat" not in r_del_est.json()["estimates"]

    r_slash_est = client.put(
        f"/devices/{did}/program-estimates",
        json={"program_name": "Pumpe/Centrifugering", "energy_kwh": 0.1, "water_litres": 0.0},
    )
    assert r_slash_est.status_code == 200
    r_del_slash = client.delete(
        f"/devices/{did}/program-estimates",
        params={"program_name": "Pumpe/Centrifugering"},
    )
    assert r_del_slash.status_code == 200
    assert "Pumpe/Centrifugering" not in r_del_slash.json()["estimates"]

    r_global = client.get("/statistics?days=30")
    assert r_global.status_code == 200
    assert "total_runs" in r_global.json()


def test_deep_sleep_404_transitions_cleanly_without_error(client_and_api):
    client, api, did = client_and_api

    async def fake_send(method, host, resource_path, **kwargs):
        if resource_path == "Devices":
            return b"{}", 200, {}
        raise MieleLocalError(
            f"Resource not found on Miele appliance: /{resource_path} (HTTP 404)",
            host=host,
            status_code=404,
        )

    with patch("miele_local.crypto.MieleCryptoProvider.send_http_request", side_effect=fake_send):
        r = client.post(f"/devices/{did}/refresh")
    assert r.status_code == 200
    dev = client.get(f"/devices/{did}").json()
    assert dev["last_error"] is None
    assert dev["state"]["DeepSleep"] is True
    assert dev["state"]["DecodedStatus"] == "Off"


def test_devices_404_reports_key_or_app_error_instead_of_fake_sleep(client_and_api):
    client, _, did = client_and_api

    async def fake_send(method, host, resource_path, **kwargs):
        raise MieleLocalError(
            f"Resource not found on Miele appliance: /{resource_path} (HTTP 404)",
            host=host,
            status_code=404,
        )

    with patch("miele_local.crypto.MieleCryptoProvider.send_http_request", side_effect=fake_send):
        r = client.post(f"/devices/{did}/refresh")
    assert r.status_code == 404
    dev = client.get(f"/devices/{did}").json()
    assert "HTTP 404" in dev["last_error"]
    assert dev["state"].get("DeepSleep") is not True


def test_cloud_oauth_start_and_exchange_routes(client_and_api):
    from miele_local.cloud import CloudGroupKey

    client, _, _ = client_and_api
    r_countries = client.get("/setup/cloud/countries")
    assert r_countries.status_code == 200
    assert "dk" in r_countries.json()["countries"]

    r_start = client.post("/setup/cloud/start", json={"country": "dk"})
    assert r_start.status_code == 200
    start_data = r_start.json()
    assert "authorize_url" in start_data
    assert start_data["country"] == "dk"

    fake_gk = CloudGroupKey(
        group_id="6BD71E44119ABF7F",
        group_key="AB" * 64,
        devices=[{"fabNumber": "000186657498"}],
        region="EU",
    )
    with patch("miele_local.routes.complete_cloud_oauth", new=AsyncMock(return_value=fake_gk)):
        r_ex = client.post(
            "/setup/cloud/exchange",
            json={"redirect_url": f"miele://oauth2-code/?code=testcode&state={start_data['state']}"},
        )
    assert r_ex.status_code == 200
    body = r_ex.json()
    assert body["success"] is True
    assert body["provisioning_info"]["GroupID"] == "6BD71E44119ABF7F"
    assert body["region"] == "EU"


