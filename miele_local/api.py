"""Core async MieleLocalAPI manager: polling, commands, DOP2, consumables, and SSE."""

import asyncio
import datetime
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import yaml

from .crypto import (
    MieleCryptoProvider,
    MieleDOP2UnavailableError,
    MieleLocalError,
    MieleProvisioningInfo,
)
from .enums import Status, SfValueId
from .state import ApplianceStateManager, build_annotated_summary

logger = logging.getLogger(__name__)


def normalize_endpoint_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "_", (name or "").strip().lower()).strip("_")
    if not slug:
        raise ValueError("Endpoint name must contain letters or digits")
    return slug


class MieleLocalAPI:
    """Coordinates Miele appliance communication, polling, DOP2, and SSE streaming."""

    def __init__(self, db_path: str, purge_history_days: Optional[int] = 30) -> None:
        self.db_path = db_path
        self.purge_history_days = purge_history_days
        self.state_manager = ApplianceStateManager(db_path)
        self.event_listeners: List[asyncio.Queue] = []
        self.device_event_listeners: List[asyncio.Queue] = []
        self.background_tasks: List[asyncio.Task] = []
        self.device_locks: Dict[int, asyncio.Lock] = {}
        self.last_update: Optional[float] = None
        self.is_shutting_down = False
        self._session: Optional[aiohttp.ClientSession] = None
        self.change_tracker = {"polls_completed": 0, "state_changes": 0, "errors": 0}

    def _get_lock(self, device_id: int) -> asyncio.Lock:
        if device_id not in self.device_locks:
            self.device_locks[device_id] = asyncio.Lock()
        return self.device_locks[device_id]

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8.0))
        return self._session

    def _provider_for(self, appliance: Dict[str, Any]) -> MieleCryptoProvider:
        info = MieleProvisioningInfo(appliance["group_id"], appliance["group_key"])
        return MieleCryptoProvider(info)

    async def start(self) -> None:
        """Start background polling and optional history purge tasks."""
        self.is_shutting_down = False
        await self._get_session()
        if self.state_manager.get_all_appliances():
            await self.refresh_all(interview=True)
        poll_task = asyncio.create_task(self._polling_loop(), name="miele-polling-loop")
        self.background_tasks.append(poll_task)
        if self.purge_history_days and self.purge_history_days >= 1:
            purge_task = asyncio.create_task(self._purge_loop(), name="miele-history-purge")
            self.background_tasks.append(purge_task)
        logger.info(
            "MieleLocalAPI started with %d configured appliance(s)",
            len(self.state_manager.get_all_appliances()),
        )

    async def cleanup(self) -> None:
        """Gracefully cancel background tasks, close SSE streams, and close HTTP session."""
        self.is_shutting_down = True
        for task in self.background_tasks:
            if not task.done():
                task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        self.background_tasks.clear()

        for queue in list(self.event_listeners):
            try:
                await queue.put(None)
            except Exception:
                pass
        self.event_listeners.clear()

        for queue in list(self.device_event_listeners):
            try:
                await queue.put(None)
            except Exception:
                pass
        self.device_event_listeners.clear()

        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("MieleLocalAPI cleanup complete")

    async def broadcast_event(self, payload: Dict[str, Any], event_type: str = "device_update") -> None:
        message = {"type": event_type, "timestamp": time.time(), **payload}
        for listeners in (self.event_listeners, self.device_event_listeners):
            for queue in list(listeners):
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    pass
                except Exception:
                    pass

    async def _polling_loop(self) -> None:
        next_poll: Dict[int, float] = {}
        while not self.is_shutting_down:
            now = time.monotonic()
            appliances = self.state_manager.get_all_appliances()
            for app in appliances:
                if not app.get("enabled", 1):
                    continue
                did = app["device_id"]
                if now >= next_poll.get(did, 0.0):
                    try:
                        summary = await self.poll_appliance(did)
                        status_val = summary.get("Status")
                        # Adaptive polling: 5s when running/waiting/paused, else configured interval
                        if status_val in (
                            Status.Running.value,
                            Status.WaitingToStart.value,
                            Status.Paused.value,
                            Status.Programmed.value,
                        ):
                            interval = min(5, max(3, int(app.get("poll_interval") or 15)))
                        else:
                            interval = max(5, int(app.get("poll_interval") or 15))
                        next_poll[did] = time.monotonic() + interval
                    except Exception as exc:
                        logger.debug("Poll failed for device %s: %s", did, exc)
                        next_poll[did] = time.monotonic() + max(15, int(app.get("poll_interval") or 15))
            await asyncio.sleep(1.0)

    async def _purge_loop(self) -> None:
        while not self.is_shutting_down:
            try:
                if self.purge_history_days:
                    deleted = self.state_manager.purge_old_history(self.purge_history_days)
                    if deleted:
                        logger.info("Purged %d old history records (>%d days)", deleted, self.purge_history_days)
            except Exception as exc:
                logger.warning("History purge failed: %s", exc)
            await asyncio.sleep(3600)

    async def autodetect_route(
        self, host: str, group_id: str, group_key: str
    ) -> str:
        """Query /Devices to resolve the 12-digit Miele device route."""
        provider = MieleCryptoProvider(MieleProvisioningInfo(group_id, group_key))
        session = await self._get_session()
        decrypted, _, _ = await provider.send_http_request("GET", host, "Devices", session=session)
        data = json.loads(decrypted.decode("utf-8"))
        if isinstance(data, dict) and len(data) >= 1:
            return list(data.keys())[0]
        raise MieleLocalError(f"Could not autodetect device route on {host}", host=host)

    async def add_or_update_appliance(
        self,
        endpoint_name: str,
        display_name: str,
        host: str,
        group_id: str,
        group_key: str,
        device_route: str = "auto",
        poll_interval: int = 15,
    ) -> Dict[str, Any]:
        slug = normalize_endpoint_name(endpoint_name)
        resolved_route = (device_route or "auto").strip() or "auto"
        if resolved_route.lower() == "auto":
            resolved_route = await self.autodetect_route(host, group_id, group_key)

        did = self.state_manager.upsert_appliance(
            endpoint_name=slug,
            display_name=display_name.strip() or slug,
            host=host.strip(),
            group_id=group_id.strip().upper(),
            group_key=group_key.strip().upper(),
            device_route=resolved_route,
            poll_interval=poll_interval,
        )
        await self.poll_appliance(did, interview=True)
        device_view = self.get_device_view(did)
        await self.broadcast_event({"device_id": did, "endpoint": slug, "device": device_view})
        return device_view

    async def refresh_all(self, interview: bool = False) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for app in self.state_manager.get_all_appliances():
            if not app.get("enabled", 1):
                continue
            try:
                await self.poll_appliance(app["device_id"], interview=interview)
            except Exception as exc:
                logger.warning("Refresh failed for %s: %s", app["endpoint_name"], exc)
            results.append(self.get_device_view(app["device_id"]))
        return results

    def _build_sleep_summary(self, app: Dict[str, Any]) -> Dict[str, Any]:
        """Build an Off/DeepSleep summary preserving known appliance identity and last program."""
        prev = dict(self.state_manager.current_state.get(app["device_id"]) or {})
        return {
            **prev,
            "Status": Status.Off.value,
            "DecodedStatus": "Off",
            "DeepSleep": True,
            "StandbyState": 2,
            "ProgramPhase": 0,
            "DecodedProgramPhase": "NotUsed",
            "RemainingMinutes": 0,
            "ElapsedMinutes": 0,
            "ProgressPercent": 0.0,
            "RemoteStartReady": False,
            "DeviceType": prev.get("DeviceType") or app.get("device_type"),
            "DecodedDeviceType": prev.get("DecodedDeviceType") or app.get("decoded_device_type"),
            "DeviceCategory": prev.get("DeviceCategory") or app.get("category") or "appliance",
        }

    async def _fetch_state_and_ident(
        self,
        app: Dict[str, Any],
        provider: MieleCryptoProvider,
        session: aiohttp.ClientSession,
        route: str,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, bool]:
        """Fetch /State and /Ident, handling Miele XKM deep-sleep 404 and route changes."""
        did = app["device_id"]
        try:
            state_bytes, _, _ = await provider.send_http_request(
                "GET", app["host"], f"Devices/{route}/State", session=session
            )
            ident_bytes, _, _ = await provider.send_http_request(
                "GET", app["host"], f"Devices/{route}/Ident", session=session
            )
            raw_state = json.loads(state_bytes.decode("utf-8")) if state_bytes else {}
            raw_ident = json.loads(ident_bytes.decode("utf-8")) if ident_bytes else {}
            return raw_state, raw_ident, route, False
        except MieleLocalError as exc:
            if exc.status_code != 404:
                raise
            # Probe /Devices to distinguish genuine Deep Sleep (200 OK with empty {})
            # from key mismatch / overwritten keys (HTTP 404 on /Devices too).
            try:
                dev_bytes, _, _ = await provider.send_http_request(
                    "GET", app["host"], "Devices", session=session, timeout=3.0
                )
                dev_map = json.loads(dev_bytes.decode("utf-8")) if dev_bytes else {}
            except MieleLocalError as probe_exc:
                if probe_exc.status_code == 404:
                    raise MieleLocalError(
                        f"Appliance at {app['host']} returned HTTP 404 on /Devices. "
                        "If the appliance is turned on, its GroupID/GroupKey may have been "
                        "overwritten by the official Miele app — re-pair with 'Keep Official App' "
                        "or '100% Local Only'.",
                        host=app["host"],
                        status_code=404,
                    ) from probe_exc
                raise exc from probe_exc

            if isinstance(dev_map, dict) and dev_map:
                new_route = next(iter(dev_map.keys()))
                if new_route != route:
                    self.state_manager.update_appliance_metadata(did, device_route=new_route)
                    state_bytes, _, _ = await provider.send_http_request(
                        "GET", app["host"], f"Devices/{new_route}/State", session=session
                    )
                    ident_bytes, _, _ = await provider.send_http_request(
                        "GET", app["host"], f"Devices/{new_route}/Ident", session=session
                    )
                    raw_state = json.loads(state_bytes.decode("utf-8")) if state_bytes else {}
                    raw_ident = json.loads(ident_bytes.decode("utf-8")) if ident_bytes else {}
                    return raw_state, raw_ident, new_route, False

            if isinstance(dev_map, dict) and not dev_map and (
                app.get("last_seen") or did in self.state_manager.current_state
            ):
                return None, None, route, True
            raise

    async def poll_appliance(
        self, identifier: Any, interview: bool = False
    ) -> Dict[str, Any]:
        app = self.state_manager.get_appliance(identifier)
        if not app:
            raise MieleLocalError(f"Appliance '{identifier}' not found", status_code=404)

        did = app["device_id"]
        lock = self._get_lock(did)
        async with lock:
            provider = self._provider_for(app)
            session = await self._get_session()
            route = app["device_route"]
            if route == "auto":
                route = await self.autodetect_route(app["host"], app["group_id"], app["group_key"])
                self.state_manager.update_appliance_metadata(did, device_route=route)

            try:
                raw_state, raw_ident, route, is_sleeping = await self._fetch_state_and_ident(
                    app, provider, session, route
                )
                now_iso = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
                if is_sleeping:
                    summary = self._build_sleep_summary(app)
                    self.state_manager.update_appliance_metadata(
                        did, last_seen=now_iso, last_error=None
                    )
                    changed = self.state_manager.record_state(did, summary)
                    self.last_update = time.time()
                    self.change_tracker["polls_completed"] += 1
                    if changed:
                        self.change_tracker["state_changes"] += 1
                        await self.broadcast_event(
                            {"device_id": did, "endpoint": app["endpoint_name"], "state": summary}
                        )
                    return summary

                assert raw_state is not None and raw_ident is not None
                summary = build_annotated_summary(raw_state, raw_ident)
                summary["DeepSleep"] = False

                ident_label = raw_ident.get("DeviceIdentLabel") or {}
                xkm_label = raw_ident.get("XKMIdentLabel") or {}
                meta_updates: Dict[str, Any] = {
                    "device_type": raw_ident.get("DeviceType"),
                    "decoded_device_type": summary.get("DecodedDeviceType"),
                    "category": summary.get("DeviceCategory"),
                    "tech_type": ident_label.get("TechType") or app.get("tech_type"),
                    "serial_number": ident_label.get("FabNumber") or app.get("serial_number"),
                    "xkm_tech_type": xkm_label.get("TechType") or app.get("xkm_tech_type"),
                    "xkm_release_version": xkm_label.get("ReleaseVersion") or app.get("xkm_release_version"),
                    "remote_start_capable": int(bool(summary.get("RemoteStartCapable"))),
                    "last_seen": now_iso,
                    "last_error": None,
                }

                # Issue #18 interview: check DOP2 availability once on setup/interview
                if interview or app.get("dop2_supported") is None:
                    try:
                        await provider.read_dop2_node(
                            app["host"], route, session=session, timeout=3.0
                        )
                        meta_updates["dop2_supported"] = 1
                    except MieleDOP2UnavailableError:
                        meta_updates["dop2_supported"] = 0
                    except Exception:
                        pass

                self.state_manager.update_appliance_metadata(did, **meta_updates)
                changed = self.state_manager.record_state(did, summary)

                # Opportunistically capture live DOP2 energy/water for running/ended cycles
                dop2_ok = meta_updates.get("dop2_supported", app.get("dop2_supported"))
                if dop2_ok == 1 and summary.get("Status") in (
                    Status.Running.value,
                    Status.EndedSuccessfully.value,
                ):
                    try:
                        fields, _ = await provider.read_dop2_leaf(
                            app["host"], 2, route, 6195, 0, 0, session=session
                        )
                        if fields:
                            from .dop2_structures import DOP2ProcessData

                            ann = DOP2ProcessData({i: f for i, f in enumerate(fields, start=1)})
                            ann.readFields()
                            pdata = ann.to_json_dict()
                            e_val = pdata.get("energyConsumed")
                            w_val = pdata.get("waterConsumedInLitres")
                            self.state_manager.update_run_consumption(
                                did,
                                float(e_val) / 1000.0 if e_val is not None else None,
                                float(w_val) if w_val is not None else None,
                                source="dop2",
                            )
                    except Exception:
                        pass

                self.last_update = time.time()
                self.change_tracker["polls_completed"] += 1
                if changed:
                    self.change_tracker["state_changes"] += 1
                    await self.broadcast_event(
                        {
                            "device_id": did,
                            "endpoint": app["endpoint_name"],
                            "state": summary,
                        }
                    )
                return summary
            except Exception as exc:
                self.change_tracker["errors"] += 1
                self.state_manager.update_appliance_metadata(did, last_error=str(exc))
                raise

    def get_device_view(self, identifier: Any) -> Dict[str, Any]:
        app = self.state_manager.get_appliance(identifier)
        if not app:
            raise MieleLocalError(f"Appliance '{identifier}' not found", status_code=404)
        did = app["device_id"]
        state = self.state_manager.current_state.get(did)
        return {
            "device_id": did,
            "endpoint": app["endpoint_name"],
            "name": app["display_name"],
            "host": app["host"],
            "route": app["device_route"],
            "device_type": app.get("device_type"),
            "decoded_device_type": app.get("decoded_device_type"),
            "category": app.get("category") or "appliance",
            "tech_type": app.get("tech_type"),
            "serial_number": app.get("serial_number"),
            "xkm_tech_type": app.get("xkm_tech_type"),
            "xkm_release_version": app.get("xkm_release_version"),
            "dop2_supported": None if app.get("dop2_supported") is None else bool(app["dop2_supported"]),
            "remote_start_capable": bool(app.get("remote_start_capable")),
            "enabled": bool(app.get("enabled", 1)),
            "poll_interval": app.get("poll_interval", 15),
            "last_seen": app.get("last_seen"),
            "last_error": app.get("last_error"),
            "state": state,
        }

    def get_all_device_views(self) -> List[Dict[str, Any]]:
        return [
            self.get_device_view(app["device_id"])
            for app in self.state_manager.get_all_appliances()
        ]

    async def send_state_command(
        self, identifier: Any, command_dict: Dict[str, Any]
    ) -> Any:
        app = self.state_manager.get_appliance(identifier)
        if not app:
            raise MieleLocalError(f"Appliance '{identifier}' not found", status_code=404)
        did = app["device_id"]
        lock = self._get_lock(did)
        async with lock:
            provider = self._provider_for(app)
            session = await self._get_session()
            payload = json.dumps(command_dict)
            decrypted, status, _ = await provider.send_http_request(
                "PUT",
                app["host"],
                f"Devices/{app['device_route']}/State",
                payload=payload,
                session=session,
            )
            if not decrypted:
                return {"status": status}
            return json.loads(decrypted.decode("utf-8", errors="replace"))

    async def start_appliance(
        self, identifier: Any, wake_first: bool = True
    ) -> Dict[str, Any]:
        """Remote start an appliance, optionally waking/switching on first (extended-wakeup-2 fix)."""
        app = self.state_manager.get_appliance(identifier)
        if not app:
            raise MieleLocalError(f"Appliance '{identifier}' not found", status_code=404)
        did = app["device_id"]
        current = self.state_manager.current_state.get(did) or {}
        if wake_first and current.get("StandbyState") in (1, 2):
            try:
                await self.send_state_command(did, {"ProcessAction": 16})
                await asyncio.sleep(0.3)
            except Exception as exc:
                logger.debug("Pre-start SwitchOn(16) ignored on %s: %s", did, exc)
        result = await self.send_state_command(did, {"ProcessAction": 1})
        try:
            await self.poll_appliance(did)
        except Exception:
            pass
        return {"result": result, "device_id": did, "endpoint": app["endpoint_name"]}

    async def stop_appliance(self, identifier: Any) -> Dict[str, Any]:
        result = await self.send_state_command(identifier, {"ProcessAction": 2})
        return {"result": result}

    async def pause_appliance(self, identifier: Any) -> Dict[str, Any]:
        result = await self.send_state_command(identifier, {"ProcessAction": 3})
        return {"result": result}

    async def wakeup_appliance(self, identifier: Any) -> Dict[str, Any]:
        result = await self.send_state_command(identifier, {"DeviceAction": 2})
        return {"result": result}

    async def set_light(self, identifier: Any, on: bool) -> Dict[str, Any]:
        result = await self.send_state_command(identifier, {"Light": 1 if on else 2})
        return {"result": result, "light": "on" if on else "off"}

    async def passthrough_get(self, identifier: Any, path: str) -> Any:
        app = self.state_manager.get_appliance(identifier)
        if not app:
            raise MieleLocalError(f"Appliance '{identifier}' not found", status_code=404)
        provider = self._provider_for(app)
        session = await self._get_session()
        async with self._get_lock(app["device_id"]):
            decrypted, _, _ = await provider.send_http_request(
                "GET", app["host"], path, session=session
            )
        if not decrypted:
            return {}
        try:
            return json.loads(decrypted.decode("utf-8"))
        except Exception:
            from .dop2 import MieleAttributeParser

            return [str(x) for x in MieleAttributeParser().parse_bytes(decrypted)]

    async def walk_dop2_tree(
        self, identifier: Any, use_cache: bool = False
    ) -> Dict[str, Any]:
        app = self.state_manager.get_appliance(identifier)
        if not app:
            raise MieleLocalError(f"Appliance '{identifier}' not found", status_code=404)
        did = app["device_id"]
        if use_cache:
            cached = self.state_manager.get_dop2_cache(did)
            if cached:
                return cached
        provider = self._provider_for(app)
        session = await self._get_session()
        async with self._get_lock(did):
            tree = await provider.read_dop2_recursive(
                app["host"], app["device_route"], session=session
            )
        self.state_manager.update_appliance_metadata(did, dop2_supported=1)
        self.state_manager.save_dop2_cache(did, tree)
        return tree

    async def read_dop2_leaf(
        self, identifier: Any, unit: int, attribute: int, idx1: int = 0, idx2: int = 0
    ) -> Dict[str, Any]:
        app = self.state_manager.get_appliance(identifier)
        if not app:
            raise MieleLocalError(f"Appliance '{identifier}' not found", status_code=404)
        provider = self._provider_for(app)
        session = await self._get_session()
        async with self._get_lock(app["device_id"]):
            fields, raw_bytes = await provider.read_dop2_leaf(
                app["host"], unit, app["device_route"], attribute, idx1, idx2, session=session
            )
        return {
            "decoded": [str(x) for x in fields],
            "fields": [x.to_dict() if hasattr(x, "to_dict") else str(x) for x in fields],
            "binary": raw_bytes.hex(" ") if raw_bytes else "",
        }

    async def write_dop2_leaf(
        self,
        identifier: Any,
        unit: int,
        attribute: int,
        payload: bytes,
        idx1: int = 0,
        idx2: int = 0,
    ) -> List[str]:
        app = self.state_manager.get_appliance(identifier)
        if not app:
            raise MieleLocalError(f"Appliance '{identifier}' not found", status_code=404)
        provider = self._provider_for(app)
        session = await self._get_session()
        async with self._get_lock(app["device_id"]):
            fields, _ = await provider.write_dop2_leaf(
                app["host"], app["device_route"], unit, attribute, payload, idx1, idx2, session=session
            )
        return [str(x) for x in fields]

    async def read_dop2_setting(self, identifier: Any, setting: str) -> Dict[str, Any]:
        try:
            setting_int = int(setting)
        except ValueError:
            setting_int = int(SfValueId[setting])
        app = self.state_manager.get_appliance(identifier)
        if not app:
            raise MieleLocalError(f"Appliance '{identifier}' not found", status_code=404)
        provider = self._provider_for(app)
        session = await self._get_session()
        async with self._get_lock(app["device_id"]):
            fields, _ = await provider.read_dop2_leaf(
                app["host"], 2, app["device_route"], 105, idx1=setting_int, idx2=0, session=session
            )
        from .dop2_structures import DOP2_SF_Value

        leaf_map = {idx: val for idx, val in enumerate(fields, start=1)}
        ann = DOP2_SF_Value(leaf_map)
        ann.readFields()
        return ann.to_json_dict()

    async def get_consumables_and_alerts(self, identifier: Any) -> Dict[str, Any]:
        """Probe fill levels, TwinDos/cartridge sensors, process consumption, and notifications (Issue #32)."""
        app = self.state_manager.get_appliance(identifier)
        if not app:
            raise MieleLocalError(f"Appliance '{identifier}' not found", status_code=404)
        did = app["device_id"]
        state = self.state_manager.current_state.get(did) or {}
        result: Dict[str, Any] = {
            "device_id": did,
            "endpoint": app["endpoint_name"],
            "decoded_device_type": app.get("decoded_device_type"),
            "signal_info": bool(state.get("SignalInfo")),
            "signal_failure": bool(state.get("SignalFailure")),
            "signal_door": bool(state.get("SignalDoor")),
            "dop2_available": False,
            "notifications": [],
            "consumables": {},
            "process_consumption": {},
            "hours_of_operation": None,
            "note": (
                "Standard Miele /State reports active alert flags (SignalInfo / SignalFailure). "
                "Granular fill levels (TwinDos, coffee tank/cartridges, water/energy) are queried "
                "via DOP2 when supported by the appliance firmware."
            ),
        }

        provider = self._provider_for(app)
        session = await self._get_session()
        route = app["device_route"]

        # Probe targeted DOP2 leaves instead of full tree walk (fast & gentle on EK057)
        probes = [
            ("notifications_131", 2, 131),
            ("notifications_392", 2, 392),
            ("failures_148", 2, 148),
            ("hours_119", 2, 119),
            ("sensors_6193", 2, 6193),
            ("process_6195", 2, 6195),
            ("device_context_1585", 2, 1585),
        ]
        async with self._get_lock(did):
            for key, unit, attr in probes:
                try:
                    fields, _ = await provider.read_dop2_leaf(
                        app["host"], unit, route, attr, 0, 0, session=session
                    )
                    if not fields:
                        continue
                    result["dop2_available"] = True
                    field_map = {idx: val for idx, val in enumerate(fields, start=1)}
                    if key in ("notifications_131", "notifications_392", "failures_148"):
                        result["notifications"].append(
                            {
                                "source": f"DOP2/{unit}/{attr}",
                                "values": [str(x) for x in fields],
                            }
                        )
                    elif key == "hours_119":
                        from .dop2_structures import DOP2HoursOfOperation

                        ann = DOP2HoursOfOperation(field_map)
                        ann.readFields()
                        result["hours_of_operation"] = ann.to_json_dict()
                    elif key == "sensors_6193":
                        from .dop2_structures import DOP2SensorData

                        ann = DOP2SensorData(field_map)
                        ann.readFields()
                        data = ann.to_json_dict()
                        result["consumables"].update(
                            {
                                k: data[k]
                                for k in (
                                    "twinDosSwitchContainer1",
                                    "twinDosSwitchContainer2",
                                    "detectedCap",
                                    "waterLevel",
                                    "dispenserDrawerSwitch",
                                )
                                if k in data
                            }
                        )
                    elif key == "process_6195":
                        from .dop2_structures import DOP2ProcessData

                        ann = DOP2ProcessData(field_map)
                        ann.readFields()
                        data = ann.to_json_dict()
                        result["process_consumption"] = {
                            k: data[k]
                            for k in ("energyConsumed", "waterConsumedInLitres", "rpmCurrent")
                            if k in data
                        }
                    elif key == "device_context_1585":
                        result["consumables"]["context_fields"] = {
                            str(k): str(v) for k, v in field_map.items()
                        }
                except MieleLocalError:
                    # Device does not expose this DOP2 leaf; stop early if 404 on DOP2 root
                    continue
                except Exception as exc:
                    logger.debug("Consumables probe %s failed on %s: %s", key, did, exc)
                    continue

        return result

    async def import_yaml_file(self, yaml_path: str) -> int:
        """Import endpoints from a legacy MieleRESTServer.config YAML file."""
        with open(yaml_path, encoding="utf-8") as fp:
            data = yaml.safe_load(fp) or {}
        endpoints_cfg = data.get("endpoints") or {}
        imported = 0
        for name, cfg in endpoints_cfg.items():
            if not isinstance(cfg, dict):
                continue
            host = cfg.get("host")
            group_id = cfg.get("groupId") or cfg.get("group_id")
            group_key = cfg.get("groupKey") or cfg.get("group_key")
            route = cfg.get("route") or "auto"
            if not (host and group_id and group_key):
                continue
            slug = normalize_endpoint_name(name)
            display_name = cfg.get("name") or name.replace("_", " ").title()
            if route == "auto":
                try:
                    route = await self.autodetect_route(host, group_id, group_key)
                except Exception as exc:
                    logger.warning("Route autodetect failed for %s (%s): %s", name, host, exc)
                    route = "auto"
            self.state_manager.upsert_appliance(
                endpoint_name=slug,
                display_name=display_name,
                host=host,
                group_id=group_id,
                group_key=group_key,
                device_route=route,
            )
            imported += 1
        return imported
