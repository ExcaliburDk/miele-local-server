"""FastAPI route handlers for Miele Local (modern REST API + MieleRESTServer compatibility)."""

import asyncio
import binascii
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .__version__ import __version__
from .cloud import CONSUMER_CLIENT_IDS, complete_cloud_oauth, start_cloud_oauth
from .crypto import (
    MieleDOP2UnavailableError,
    MieleLocalError,
    MieleProvisioningInfo,
    provision_keys_on_device,
    provision_wifi_on_ap,
)
from .state import build_annotated_summary

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)
API_KEYS_RAW = os.environ.get("MIELE_API_KEYS", "").strip()
API_KEYS = set(k.strip() for k in API_KEYS_RAW.split() if k.strip()) if API_KEYS_RAW else set()


def get_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[str]:
    """Validate optional Bearer token from MIELE_API_KEYS."""
    if not API_KEYS:
        return None
    if not credentials or credentials.credentials not in API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


def create_app() -> FastAPI:
    app = FastAPI(
        title="Miele Local",
        description="Local REST API, Web UI, and SSE event stream for Miele@home appliances",
        version=__version__,
    )
    if API_KEYS:
        logger.info("API authentication enabled (%d key(s) configured)", len(API_KEYS))
    else:
        logger.info("API authentication disabled (no MIELE_API_KEYS configured)")

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.exception_handler(MieleLocalError)
    async def miele_error_handler(_request: Request, exc: MieleLocalError):
        return JSONResponse(status_code=exc.status_code, content=exc.asdict())

    return app


class ApplianceCreateRequest(BaseModel):
    endpoint_name: str = Field(min_length=1, max_length=64)
    display_name: Optional[str] = Field(default=None, max_length=120)
    host: str = Field(min_length=1, max_length=255)
    group_id: str = Field(min_length=16, max_length=16)
    group_key: str = Field(min_length=128, max_length=128)
    route: str = Field(default="auto", max_length=32)
    poll_interval: int = Field(default=15, ge=3, le=3600)


class ApplianceUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)
    host: Optional[str] = Field(default=None, max_length=255)
    route: Optional[str] = Field(default=None, max_length=32)
    poll_interval: Optional[int] = Field(default=None, ge=3, le=3600)
    enabled: Optional[bool] = None


class CommissionRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    endpoint_name: str = Field(default="washer", min_length=1, max_length=64)
    display_name: Optional[str] = Field(default=None, max_length=120)
    group_id: Optional[str] = None
    group_key: Optional[str] = None
    save_on_success: bool = True


class WifiProvisionRequest(BaseModel):
    ap_host: str = Field(default="192.168.1.1", min_length=1, max_length=255)
    ssid: str = Field(min_length=1, max_length=128)
    security: str = Field(default="WPA2", max_length=32)
    wifi_key: str = Field(default="", max_length=128)


class LightControlRequest(BaseModel):
    on: bool


class StartControlRequest(BaseModel):
    wake_first: bool = True


class HomeNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class YamlImportRequest(BaseModel):
    yaml_content: str = Field(min_length=1)


class ProgramEstimateRequest(BaseModel):
    program_name: str = Field(min_length=1, max_length=100)
    energy_kwh: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    water_litres: Optional[float] = Field(default=None, ge=0.0, le=1000.0)


class CloudStartRequest(BaseModel):
    country: str = Field(default="dk", min_length=2, max_length=5)


class CloudExchangeRequest(BaseModel):
    redirect_url: str = Field(min_length=4)
    state: Optional[str] = None
    host: Optional[str] = Field(default=None, max_length=255)
    endpoint_name: str = Field(default="washer", min_length=1, max_length=64)
    display_name: Optional[str] = Field(default=None, max_length=120)
    route: str = Field(default="auto", max_length=32)
    save_on_success: bool = True


def _get_home_name(miele_api) -> str:
    env_name = os.getenv("MIELE_HOME_NAME") or os.getenv("HOME_NAME")
    db_name = miele_api.state_manager.get_setting("home_name")
    return db_name or env_name or "Miele Local Server"


def register_routes(app: FastAPI, get_miele_api) -> None:
    """Register all modern REST, Web UI, setup, SSE, and MieleRESTServer-compatible routes."""

    @app.get("/", include_in_schema=False)
    async def root():
        static_dir = Path(__file__).parent / "static"
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(index_file, media_type="text/html")
        return {
            "service": "Miele Local",
            "version": __version__,
            "documentation": "/docs",
            "api_info": "/api",
        }

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        static_dir = Path(__file__).parent / "static"
        svg = static_dir / "favicon.svg"
        if svg.exists():
            return FileResponse(svg, media_type="image/svg+xml")
        raise HTTPException(status_code=404, detail="Favicon not found")

    @app.get("/robots.txt", include_in_schema=False)
    async def robots():
        static_dir = Path(__file__).parent / "static"
        robots_file = static_dir / "robots.txt"
        if robots_file.exists():
            return FileResponse(robots_file, media_type="text/plain")
        return PlainTextResponse("User-agent: *\nDisallow: /\n")

    @app.get("/.well-known/{path:path}", include_in_schema=False)
    async def well_known(path: str):
        raise HTTPException(status_code=404, detail="Not found")

    @app.get("/api", tags=["Info"])
    async def api_info(api_key: Optional[str] = Depends(get_api_key)):
        return {
            "service": "Miele Local",
            "description": "Local REST API and Web UI for Miele@home appliances",
            "version": __version__,
            "documentation": "/docs",
            "web_ui": "/",
            "endpoints": {
                "status": "/status",
                "health": "/health",
                "devices": "/devices",
                "events": "/events",
                "setup": "/setup/status",
                "refresh": "/refresh",
                "legacy_summary": "/generate-summary",
            },
        }

    @app.get("/health", tags=["Info"])
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/status", tags=["Status"])
    async def get_status(api_key: Optional[str] = Depends(get_api_key)):
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        devices = miele_api.state_manager.get_all_appliances()
        return {
            "status": "ready" if devices else "needs_setup",
            "version": __version__,
            "home_name": _get_home_name(miele_api),
            "configured_devices": len(devices),
            "last_update": miele_api.last_update,
            "active_listeners": len(miele_api.event_listeners),
            "polls_completed": miele_api.change_tracker.get("polls_completed", 0),
            "state_changes": miele_api.change_tracker.get("state_changes", 0),
            "errors": miele_api.change_tracker.get("errors", 0),
        }

    @app.get("/settings/home-name", tags=["Settings"])
    async def get_home_name(api_key: Optional[str] = Depends(get_api_key)):
        miele_api = get_miele_api()
        return {"name": _get_home_name(miele_api)}

    @app.put("/settings/home-name", tags=["Settings"])
    async def set_home_name(req: HomeNameRequest, api_key: Optional[str] = Depends(get_api_key)):
        miele_api = get_miele_api()
        miele_api.state_manager.set_setting("home_name", req.name.strip())
        return {"name": req.name.strip()}

    # -------------------------------------------------------------------------
    # Setup & Provisioning Endpoints (integrated config_wizard + keygen)
    # -------------------------------------------------------------------------

    @app.get("/setup/status", tags=["Setup"])
    async def setup_status():
        miele_api = get_miele_api()
        devices = miele_api.state_manager.get_all_appliances() if miele_api else []
        return {
            "status": "ready" if devices else "waiting",
            "configured_count": len(devices),
            "message": (
                "Appliances configured."
                if devices
                else "Add a Miele appliance with existing keys or commission a new one on your LAN."
            ),
        }

    @app.post("/setup/generate-keys", tags=["Setup"])
    async def setup_generate_keys(api_key: Optional[str] = Depends(get_api_key)):
        info = MieleProvisioningInfo.generate_random()
        return info.to_dict()

    @app.get("/setup/cloud/countries", tags=["Setup"])
    async def setup_cloud_countries(api_key: Optional[str] = Depends(get_api_key)):
        return {"default": "dk", "countries": sorted(CONSUMER_CLIENT_IDS.keys())}

    @app.post("/setup/cloud/start", tags=["Setup"])
    async def setup_cloud_start(
        req: CloudStartRequest, api_key: Optional[str] = Depends(get_api_key)
    ):
        import aiohttp

        miele_api = get_miele_api()
        session = await miele_api._get_session() if miele_api else aiohttp.ClientSession()
        try:
            return await start_cloud_oauth(session, req.country)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            if not miele_api:
                await session.close()

    @app.post("/setup/cloud/exchange", tags=["Setup"])
    async def setup_cloud_exchange(
        req: CloudExchangeRequest, api_key: Optional[str] = Depends(get_api_key)
    ):
        import aiohttp

        miele_api = get_miele_api()
        session = await miele_api._get_session() if miele_api else aiohttp.ClientSession()
        try:
            gk = await complete_cloud_oauth(
                session,
                redirect_url=req.redirect_url,
                state=req.state,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            if not miele_api:
                await session.close()

        result: Dict[str, Any] = {
            "success": True,
            "provisioning_info": {"GroupID": gk.group_id, "GroupKey": gk.group_key},
            "region": gk.region,
            "cloud_devices": gk.devices,
        }
        if req.host and req.host.strip() and req.save_on_success and miele_api:
            try:
                device = await miele_api.add_or_update_appliance(
                    endpoint_name=req.endpoint_name,
                    display_name=req.display_name or req.endpoint_name.replace("_", " ").title(),
                    host=req.host.strip(),
                    group_id=gk.group_id,
                    group_key=gk.group_key,
                    device_route=req.route or "auto",
                )
                result["device"] = device
            except Exception as exc:
                result["save_warning"] = (
                    f"Cloud keys retrieved, but connecting to {req.host.strip()} failed: {exc}"
                )
        return result

    @app.post("/setup/commission", tags=["Setup"])
    async def setup_commission(
        req: CommissionRequest, api_key: Optional[str] = Depends(get_api_key)
    ):
        miele_api = get_miele_api()
        info = (
            MieleProvisioningInfo(req.group_id, req.group_key)
            if req.group_id and req.group_key
            else MieleProvisioningInfo.generate_random()
        )
        result = await provision_keys_on_device(req.host.strip(), info)
        if result["success"] and req.save_on_success and miele_api:
            try:
                device = await miele_api.add_or_update_appliance(
                    endpoint_name=req.endpoint_name,
                    display_name=req.display_name or req.endpoint_name.replace("_", " ").title(),
                    host=req.host.strip(),
                    group_id=info.groupid,
                    group_key=info.groupkey_hex,
                    device_route="auto",
                )
                result["device"] = device
            except Exception as exc:
                result["save_warning"] = (
                    f"Keys were uploaded to appliance, but initial query failed: {exc}"
                )
        return result

    @app.post("/setup/provision-wifi", tags=["Setup"])
    async def setup_provision_wifi(
        req: WifiProvisionRequest, api_key: Optional[str] = Depends(get_api_key)
    ):
        return await provision_wifi_on_ap(
            ap_host=req.ap_host.strip(),
            ssid=req.ssid.strip(),
            security=req.security.strip(),
            wifi_key=req.wifi_key,
        )

    @app.post("/setup/pair", tags=["Setup"])
    async def setup_pair(
        req: ApplianceCreateRequest, api_key: Optional[str] = Depends(get_api_key)
    ):
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        device = await miele_api.add_or_update_appliance(
            endpoint_name=req.endpoint_name,
            display_name=req.display_name or req.endpoint_name.replace("_", " ").title(),
            host=req.host,
            group_id=req.group_id,
            group_key=req.group_key,
            device_route=req.route,
            poll_interval=req.poll_interval,
        )
        return {"status": "connected", "device": device}

    @app.post("/setup/import-yaml", tags=["Setup"])
    async def setup_import_yaml(
        req: YamlImportRequest, api_key: Optional[str] = Depends(get_api_key)
    ):
        import tempfile

        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tmp:
            tmp.write(req.yaml_content)
            tmp_path = tmp.name
        try:
            count = await miele_api.import_yaml_file(tmp_path)
            await miele_api.refresh_all(interview=True)
            return {"imported": count, "devices": miele_api.get_all_device_views()}
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # -------------------------------------------------------------------------
    # Modern Devices API (Tado-Local style)
    # -------------------------------------------------------------------------

    @app.get("/devices", tags=["Devices"])
    async def get_devices(api_key: Optional[str] = Depends(get_api_key)):
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        devices = miele_api.get_all_device_views()
        return {
            "homes": [{"id": 1, "name": _get_home_name(miele_api)}],
            "devices": devices,
            "count": len(devices),
        }

    @app.post("/devices", tags=["Devices"])
    async def create_device(
        req: ApplianceCreateRequest, api_key: Optional[str] = Depends(get_api_key)
    ):
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        device = await miele_api.add_or_update_appliance(
            endpoint_name=req.endpoint_name,
            display_name=req.display_name or req.endpoint_name.replace("_", " ").title(),
            host=req.host,
            group_id=req.group_id,
            group_key=req.group_key,
            device_route=req.route,
            poll_interval=req.poll_interval,
        )
        return device

    @app.get("/devices/{identifier}", tags=["Devices"])
    async def get_device(identifier: str, api_key: Optional[str] = Depends(get_api_key)):
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        return miele_api.get_device_view(identifier)

    @app.patch("/devices/{identifier}", tags=["Devices"])
    async def update_device(
        identifier: str,
        req: ApplianceUpdateRequest,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        app_rec = miele_api.state_manager.get_appliance(identifier)
        if not app_rec:
            raise HTTPException(status_code=404, detail=f"Appliance '{identifier}' not found")
        updates: Dict[str, Any] = {}
        if req.display_name is not None:
            updates["display_name"] = req.display_name.strip()
        if req.host is not None:
            updates["host"] = req.host.strip()
        if req.route is not None:
            updates["device_route"] = req.route.strip()
        if req.poll_interval is not None:
            updates["poll_interval"] = req.poll_interval
        if req.enabled is not None:
            updates["enabled"] = int(req.enabled)
        miele_api.state_manager.update_appliance_metadata(app_rec["device_id"], **updates)
        return miele_api.get_device_view(app_rec["device_id"])

    @app.delete("/devices/{identifier}", tags=["Devices"])
    async def delete_device(identifier: str, api_key: Optional[str] = Depends(get_api_key)):
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        app_rec = miele_api.state_manager.get_appliance(identifier)
        if not app_rec:
            raise HTTPException(status_code=404, detail=f"Appliance '{identifier}' not found")
        miele_api.state_manager.delete_appliance(app_rec["device_id"])
        return {"deleted": True, "device_id": app_rec["device_id"]}

    @app.post("/devices/{identifier}/refresh", tags=["Devices"])
    async def refresh_device(identifier: str, api_key: Optional[str] = Depends(get_api_key)):
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        await miele_api.poll_appliance(identifier)
        return miele_api.get_device_view(identifier)

    @app.get("/devices/{identifier}/history", tags=["Devices"])
    async def get_device_history(
        identifier: str,
        hours: int = 24,
        limit: int = 1000,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        app_rec = miele_api.state_manager.get_appliance(identifier)
        if not app_rec:
            raise HTTPException(status_code=404, detail=f"Appliance '{identifier}' not found")
        history = miele_api.state_manager.get_history(
            app_rec["device_id"], hours=max(1, min(hours, 8760)), limit=max(1, min(limit, 5000))
        )
        return {
            "device_id": app_rec["device_id"],
            "endpoint": app_rec["endpoint_name"],
            "hours": hours,
            "count": len(history),
            "history": history,
        }

    @app.get("/statistics", tags=["Statistics"])
    async def get_global_statistics(
        days: int = 30, api_key: Optional[str] = Depends(get_api_key)
    ):
        """Aggregate cycle statistics across all configured appliances."""
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        return miele_api.state_manager.get_statistics(device_id=None, days=max(0, min(days, 3650)))

    @app.get("/devices/{identifier}/statistics", tags=["Statistics"])
    async def get_device_statistics(
        identifier: str, days: int = 30, api_key: Optional[str] = Depends(get_api_key)
    ):
        """Get local cycle statistics, program breakdown, and recent runs for an appliance."""
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        app_rec = miele_api.state_manager.get_appliance(identifier)
        if not app_rec:
            raise HTTPException(status_code=404, detail=f"Appliance '{identifier}' not found")
        return miele_api.state_manager.get_statistics(
            device_id=app_rec["device_id"], days=max(0, min(days, 3650))
        )

    @app.put("/devices/{identifier}/program-estimates", tags=["Statistics"])
    async def save_program_estimate(
        identifier: str,
        req: ProgramEstimateRequest,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        """Save optional estimated kWh / water litres per program for non-DOP2 appliances."""
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        app_rec = miele_api.state_manager.get_appliance(identifier)
        if not app_rec:
            raise HTTPException(status_code=404, detail=f"Appliance '{identifier}' not found")
        miele_api.state_manager.set_program_estimate(
            app_rec["device_id"], req.program_name, req.energy_kwh, req.water_litres
        )
        return {"saved": True, "estimates": miele_api.state_manager.get_program_estimates(app_rec["device_id"])}

    def _delete_estimate_response(identifier: str, program_name: str) -> Dict[str, Any]:
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        app_rec = miele_api.state_manager.get_appliance(identifier)
        if not app_rec:
            raise HTTPException(status_code=404, detail=f"Appliance '{identifier}' not found")
        deleted = miele_api.state_manager.delete_program_estimate(app_rec["device_id"], program_name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Program estimate '{program_name}' not found")
        return {"deleted": True, "estimates": miele_api.state_manager.get_program_estimates(app_rec["device_id"])}

    @app.delete("/devices/{identifier}/program-estimates", tags=["Statistics"])
    async def delete_program_estimate_query(
        identifier: str,
        program_name: str,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        """Delete a saved per-program energy/water estimate (safe for names containing '/')."""
        return _delete_estimate_response(identifier, program_name)

    @app.delete("/devices/{identifier}/program-estimates/{program_name:path}", tags=["Statistics"])
    async def delete_program_estimate(
        identifier: str,
        program_name: str,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        """Delete a saved per-program energy/water estimate."""
        return _delete_estimate_response(identifier, program_name)

    @app.delete("/devices/{identifier}/runs/{run_id}", tags=["Statistics"])
    async def delete_device_run(
        identifier: str, run_id: int, api_key: Optional[str] = Depends(get_api_key)
    ):
        miele_api = get_miele_api()
        if not miele_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        app_rec = miele_api.state_manager.get_appliance(identifier)
        if not app_rec:
            raise HTTPException(status_code=404, detail=f"Appliance '{identifier}' not found")
        deleted = miele_api.state_manager.delete_run(app_rec["device_id"], run_id)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
        return {"deleted": True, "run_id": run_id}

    @app.post("/devices/{identifier}/start", tags=["Control"])
    async def device_start(
        identifier: str,
        req: Optional[StartControlRequest] = None,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        miele_api = get_miele_api()
        wake_first = req.wake_first if req else True
        return await miele_api.start_appliance(identifier, wake_first=wake_first)

    @app.post("/devices/{identifier}/stop", tags=["Control"])
    async def device_stop(identifier: str, api_key: Optional[str] = Depends(get_api_key)):
        return await get_miele_api().stop_appliance(identifier)

    @app.post("/devices/{identifier}/pause", tags=["Control"])
    async def device_pause(identifier: str, api_key: Optional[str] = Depends(get_api_key)):
        return await get_miele_api().pause_appliance(identifier)

    @app.post("/devices/{identifier}/wakeup", tags=["Control"])
    async def device_wakeup(identifier: str, api_key: Optional[str] = Depends(get_api_key)):
        return await get_miele_api().wakeup_appliance(identifier)

    @app.post("/devices/{identifier}/light", tags=["Control"])
    async def device_light(
        identifier: str,
        req: LightControlRequest,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        return await get_miele_api().set_light(identifier, on=req.on)

    @app.get("/devices/{identifier}/consumables", tags=["Consumables & Alerts"])
    async def device_consumables(
        identifier: str, api_key: Optional[str] = Depends(get_api_key)
    ):
        """Probe active alerts, TwinDos/cartridge fill levels, hours of operation, and energy/water."""
        return await get_miele_api().get_consumables_and_alerts(identifier)

    @app.get("/devices/{identifier}/dop2", tags=["DOP2"])
    async def device_dop2_tree(
        identifier: str,
        cached: bool = False,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        return await get_miele_api().walk_dop2_tree(identifier, use_cache=cached)

    @app.get("/devices/{identifier}/dop2/{unit}/{attribute}", tags=["DOP2"])
    async def device_dop2_leaf_get(
        identifier: str,
        unit: int,
        attribute: int,
        idx1: int = 0,
        idx2: int = 0,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        return await get_miele_api().read_dop2_leaf(identifier, unit, attribute, idx1, idx2)

    @app.post("/devices/{identifier}/dop2/{unit}/{attribute}", tags=["DOP2"])
    async def device_dop2_leaf_post(
        identifier: str,
        unit: int,
        attribute: int,
        request: Request,
        idx1: int = 0,
        idx2: int = 0,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        payload = await request.body()
        if request.headers.get("content-type", "").startswith("text/plain"):
            payload = binascii.unhexlify(payload.strip())
        return await get_miele_api().write_dop2_leaf(identifier, unit, attribute, payload, idx1, idx2)

    @app.post("/refresh", tags=["Devices"])
    async def refresh_all(api_key: Optional[str] = Depends(get_api_key)):
        miele_api = get_miele_api()
        devices = await miele_api.refresh_all()
        return {"refreshed": len(devices), "devices": devices}

    # -------------------------------------------------------------------------
    # Server-Sent Events (SSE)
    # -------------------------------------------------------------------------

    async def _sse_stream(listeners_attr: str):
        miele_api = get_miele_api()
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        listeners = getattr(miele_api, listeners_attr)
        listeners.append(queue)
        try:
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': time.time()})}\n\n"
            while not miele_api.is_shutting_down:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                    if msg is None:
                        break
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield f": keepalive {int(time.time())}\n\n"
        finally:
            if queue in listeners:
                listeners.remove(queue)

    @app.get("/events", tags=["Events"])
    async def sse_events(api_key: Optional[str] = Depends(get_api_key)):
        return StreamingResponse(_sse_stream("event_listeners"), media_type="text/event-stream")

    @app.get("/events/devices", tags=["Events"])
    async def sse_device_events(api_key: Optional[str] = Depends(get_api_key)):
        return StreamingResponse(
            _sse_stream("device_event_listeners"), media_type="text/event-stream"
        )

    # -------------------------------------------------------------------------
    # Backward-Compatible MieleRESTServer Routes
    # -------------------------------------------------------------------------

    @app.get("/generate-summary", tags=["Legacy MieleRESTServer"])
    async def legacy_generate_summary_all(api_key: Optional[str] = Depends(get_api_key)):
        miele_api = get_miele_api()
        result: Dict[str, Any] = {}
        for app_rec in miele_api.state_manager.get_all_appliances():
            did = app_rec["device_id"]
            try:
                summary = await miele_api.poll_appliance(did)
            except Exception:
                summary = miele_api.state_manager.current_state.get(did) or build_annotated_summary({})
            result[app_rec["endpoint_name"]] = summary
        return result

    @app.get("/generate-summary/{endpoint}", tags=["Legacy MieleRESTServer"])
    async def legacy_generate_summary_one(
        endpoint: str, api_key: Optional[str] = Depends(get_api_key)
    ):
        return await get_miele_api().poll_appliance(endpoint)

    @app.get("/endpoints", tags=["Legacy MieleRESTServer"])
    async def legacy_endpoints(api_key: Optional[str] = Depends(get_api_key)):
        miele_api = get_miele_api()
        return {
            app_rec["endpoint_name"]: {
                "host": app_rec["host"],
                "groupid": app_rec["group_id"],
                "route": app_rec["device_route"],
                "last_comm": app_rec.get("last_seen") or "never",
            }
            for app_rec in miele_api.state_manager.get_all_appliances()
        }

    @app.get("/endpoints/{endpoint}", tags=["Legacy MieleRESTServer"])
    async def legacy_endpoint_one(
        endpoint: str, api_key: Optional[str] = Depends(get_api_key)
    ):
        app_rec = get_miele_api().state_manager.get_appliance(endpoint)
        if not app_rec:
            raise HTTPException(status_code=404, detail=f"Endpoint '{endpoint}' not found")
        return {
            "host": app_rec["host"],
            "groupid": app_rec["group_id"],
            "route": app_rec["device_route"],
            "last_comm": app_rec.get("last_seen") or "never",
        }

    @app.get("/start/{endpoint}", tags=["Legacy MieleRESTServer"])
    async def legacy_start_get(endpoint: str, api_key: Optional[str] = Depends(get_api_key)):
        summary = await get_miele_api().poll_appliance(endpoint)
        return {
            "DeviceRemoteStartCapable": summary.get("RemoteStartCapable", False),
            "DeviceRemoteStartEnabled": summary.get("RemoteStartReady", False),
            "Status": summary.get("DecodedStatus"),
            "message": "Set and start timer or MobileStart on device if not currently enabled",
        }

    @app.post("/start/{endpoint}", tags=["Legacy MieleRESTServer"])
    async def legacy_start_post(endpoint: str, api_key: Optional[str] = Depends(get_api_key)):
        res = await get_miele_api().start_appliance(endpoint, wake_first=True)
        return res["result"]

    @app.post("/wakeup/{endpoint}", tags=["Legacy MieleRESTServer"])
    async def legacy_wakeup_post(endpoint: str, api_key: Optional[str] = Depends(get_api_key)):
        res = await get_miele_api().wakeup_appliance(endpoint)
        return res["result"]

    @app.get("/walkdop2tree/{endpoint}", tags=["Legacy MieleRESTServer"])
    async def legacy_walk_dop2(endpoint: str, api_key: Optional[str] = Depends(get_api_key)):
        try:
            return await get_miele_api().walk_dop2_tree(endpoint)
        except MieleDOP2UnavailableError as exc:
            return JSONResponse(status_code=404, content=exc.asdict())

    @app.get("/dop2leaf/{endpoint}/{unit}/{attribute}", tags=["Legacy MieleRESTServer"])
    @app.get("/dop2leaf/{endpoint}/{unit}/{attribute}/{idx1}", tags=["Legacy MieleRESTServer"])
    @app.get(
        "/dop2leaf/{endpoint}/{unit}/{attribute}/{idx1}/{idx2}",
        tags=["Legacy MieleRESTServer"],
    )
    async def legacy_dop2_leaf_get(
        endpoint: str,
        unit: int,
        attribute: int,
        idx1: int = 0,
        idx2: int = 0,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        return await get_miele_api().read_dop2_leaf(endpoint, unit, attribute, idx1, idx2)

    @app.post("/dop2leaf/{endpoint}/{unit}/{attribute}", tags=["Legacy MieleRESTServer"])
    @app.post("/dop2leaf/{endpoint}/{unit}/{attribute}/{idx1}", tags=["Legacy MieleRESTServer"])
    @app.post(
        "/dop2leaf/{endpoint}/{unit}/{attribute}/{idx1}/{idx2}",
        tags=["Legacy MieleRESTServer"],
    )
    async def legacy_dop2_leaf_post(
        endpoint: str,
        unit: int,
        attribute: int,
        request: Request,
        idx1: int = 0,
        idx2: int = 0,
        api_key: Optional[str] = Depends(get_api_key),
    ):
        payload = await request.body()
        if request.headers.get("content-type", "").startswith("text/plain"):
            payload = binascii.unhexlify(payload.strip())
        return await get_miele_api().write_dop2_leaf(endpoint, unit, attribute, payload, idx1, idx2)

    @app.get("/dop2setting/{endpoint}/{setting}", tags=["Legacy MieleRESTServer"])
    async def legacy_dop2_setting(
        endpoint: str, setting: str, api_key: Optional[str] = Depends(get_api_key)
    ):
        return await get_miele_api().read_dop2_setting(endpoint, setting)

    @app.get("/command/{endpoint}/{command}", tags=["Legacy MieleRESTServer"])
    async def legacy_command(
        endpoint: str, command: str, api_key: Optional[str] = Depends(get_api_key)
    ):
        clean = command.replace("_", "/").replace("-", "")
        return await get_miele_api().passthrough_get(endpoint, clean)
