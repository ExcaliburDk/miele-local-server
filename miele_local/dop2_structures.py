"""DOP2 structured annotators for Miele@home devices."""

from binascii import hexlify
from typing import Any, Dict, List

from .enums import (
    ApplianceState,
    DeviceType,
    OperationState,
    ProcessState,
    ProtocolType,
    SfValueId,
)


class DOP2Annotator(dict):
    """Base annotator converting parsed DOP2 field maps into JSON-safe dictionaries."""

    def __init__(self, tree: Dict[int, Any]) -> None:
        self.tree = tree
        super().__init__()

    def getIntAtIndex(self, i: int) -> int:
        return int(self.tree[i].value)

    def getAtIndex(self, i: int) -> str:
        return str(self.tree[i].value)

    def getArrayToStrAtIndex(self, i: int) -> List[Any]:
        return [x.value for x in self.tree[i].value]

    def getStringAtIndex(self, i: int) -> str:
        val = self.tree[i].value
        if isinstance(val, (bytes, bytearray)):
            return val.decode(encoding="utf-8", errors="ignore").strip("\x00")
        return str(val).strip("\x00")

    def getWifiKeyAtIndex(self, i: int) -> str:
        return self.getStringAtIndex(i).replace("\x07", "*")

    def getStructAtIndex(self, i: int) -> List[Any]:
        return self.tree[i].value

    def getBoolAtIndex(self, i: int) -> bool:
        return bool(self.tree[i].value)

    def getEnumAtIndex(self, i: int) -> Any:
        return self.tree[i].value

    def getValueWithInterpretationAtIndex(self, i: int) -> Any:
        _, value, _ = self.getStructAtIndex(i)
        return value.value

    def getBytesAtIndex(self, i: int) -> str:
        val = self.tree[i].value
        if isinstance(val, (bytes, bytearray)):
            return hexlify(val, " ").decode("ascii")
        return str(val)

    def getEnumAsStrAtIndex(self, i: int, enum_type) -> str:
        try:
            return str(enum_type(self.getIntAtIndex(i)).name)
        except Exception:
            return f"UNKNOWN: {self.getAtIndex(i)}"

    def getEnumFlagsAtIndex(self, i: int, enum_type) -> Any:
        try:
            return [str(enum_type(x.value).name) for x in self.tree[i].value]
        except Exception as exc:
            return f"UNKNOWN: {exc}"

    def getIpAtIndex(self, i: int) -> str:
        tup = list(self.tree[i].value)
        if len(tup) != 4:
            raise ValueError("not an IPv4 address")
        return f"{tup[0]}.{tup[1]}.{tup[2]}.{tup[3]}"

    def to_json_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in self.items():
            if isinstance(v, (bytes, bytearray)):
                out[k] = hexlify(v).decode("ascii")
            elif isinstance(v, (str, int, float, bool)) or v is None:
                out[k] = v
            elif isinstance(v, list):
                out[k] = [
                    x.decode("utf-8", errors="ignore") if isinstance(x, (bytes, bytearray)) else x
                    for x in v
                ]
            else:
                out[k] = str(v)
        return out


class DOP2_SF_Value(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 105]

    def readFields(self) -> None:
        self["enumSfId"] = self.getEnumAsStrAtIndex(1, SfValueId)
        self["validity"] = self.getAtIndex(2)
        self["enumValueInterpretation"] = self.getAtIndex(3)
        for count, name in enumerate(["currentValue", "min", "max", "default"], start=4):
            self[name] = self.getAtIndex(count)
        if 7 in self.tree:
            self["listRef"] = self.getAtIndex(7)
        if 9 in self.tree:
            self["stepSize"] = self.getAtIndex(9)
        if 10 in self.tree:
            self["extValue"] = self.getBoolAtIndex(10)
        if 11 in self.tree:
            self["fineAdjusted"] = self.getBoolAtIndex(11)


class DOP2LastUpdateInfo(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [15, 199]

    def readFields(self) -> None:
        self["filename"] = self.getStringAtIndex(1)


class DOPUpdateContainerInformation(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [15, 397]

    def readFields(self) -> None:
        self["updateState"] = self.getAtIndex(1)
        self["crc32"] = self.getAtIndex(11)


class ProgramInfoCA(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 213]

    def readFields(self) -> None:
        self["startDelay"] = self.getAtIndex(5)


class ProgramStepInfoCA(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 214]

    def readFields(self) -> None:
        self["operationMode"] = self.getAtIndex(5)


class ProgramName(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 216]

    def readFields(self) -> None:
        self["programName"] = self.getAtIndex(3)


class ProgramInstructionsCA(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 257]

    def readFields(self) -> None:
        self["infoId"] = self.getAtIndex(1)
        self["messageId"] = self.getAtIndex(2)


class DOP2UserRequest(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 1583]

    def readFields(self) -> None:
        self["userRequestId"] = self.getEnumAtIndex(1)
        self["parameter0"] = self.getAtIndex(2)
        self["parameter1"] = self.getAtIndex(3)


class DOP2UpdateControl(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [15, 170]

    def readFields(self) -> None:
        self["updateState"] = self.getEnumAtIndex(1)
        self["filename"] = self.getStringAtIndex(2)
        self["flashAccessible"] = self.getAtIndex(3)
        self["progress"] = self.getAtIndex(4)


class DOP2SoftwareIds(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [1, 17]

    def readFields(self) -> None:
        self["numberValidSoftwareIds"] = self.getAtIndex(1)


class DOP2NotificationShow(DOP2Annotator):
    """GLOBAL_NTFCTN_Show - Active appliance notifications/alerts (e.g. fill salt/rinse aid)."""

    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 131]

    def readFields(self) -> None:
        if 1 in self.tree:
            self["messageId"] = self.getAtIndex(1)
        if 2 in self.tree:
            self["messages"] = str(self.tree[2])
        if 3 in self.tree:
            self["errors"] = str(self.tree[3])


class DOP2NotificationShowAlt(DOP2NotificationShow):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 392]


class DOP2FileList(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [1, 333]

    def readFields(self) -> None:
        self["fileName"] = self.getStringAtIndex(1)
        self["sha256"] = self.getBytesAtIndex(2)
        self["description"] = self.getStringAtIndex(3)
        self["fileAccessMode"] = self.getAtIndex(4)


class DOP2FileInfo(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [15, 1588]

    def readFields(self) -> None:
        self["fileName"] = self.getStringAtIndex(1)
        self["sha256"] = self.getBytesAtIndex(2)
        self["currentSize"] = self.getAtIndex(3)
        self["maxSize"] = self.getAtIndex(4)
        self["crc32"] = self.getAtIndex(5)


class DOP2FileWrite(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [15, 1590]

    def readFields(self) -> None:
        self["fileOperation"] = self.getStringAtIndex(1)
        self["fileName"] = self.getStringAtIndex(2)
        self["address"] = self.getStringAtIndex(3)
        self["size"] = self.getStringAtIndex(4)
        self["data"] = self.getStringAtIndex(5)


class DOP2RSAPublicKey(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [15, 287]

    def readFields(self) -> None:
        self["rsaPublicKey"] = self.getBytesAtIndex(1)


class DOP2DeviceCombinedState(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 1586]

    def readFields(self) -> None:
        self["applianceState"] = self.getEnumAsStrAtIndex(1, ApplianceState)
        self["operationState"] = self.getEnumAsStrAtIndex(2, OperationState)
        self["processState"] = self.getEnumAsStrAtIndex(3, ProcessState)


class DOP2_PS_Select(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 1577]

    def readFields(self) -> None:
        self["programId"] = self.getAtIndex(1)
        if 2 in self.tree:
            self["selectionParameter"] = self.getAtIndex(2)
        if 3 in self.tree:
            self["selectionType"] = self.getAtIndex(3)


class DOP2ProgramList(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 1584]

    def readFields(self) -> None:
        self["valid"] = self.getAtIndex(1)
        self["programIds"] = self.getArrayToStrAtIndex(2)


class DOP2DeviceContext(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 1585]

    def readFields(self) -> None:
        self["deviceCombinedState"] = self.getAtIndex(1)
        if 11 in self.tree:
            self["mobileStartActive"] = self.getBoolAtIndex(11)


class DOP2SecondDeviceContext(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 391]

    def readFields(self) -> None:
        self["deviceCombinedState"] = self.getAtIndex(1)
        if 11 in self.tree:
            self["supportedUserRequest"] = self.getAtIndex(11)


class DOP2NotificationAcknowledge(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 138]

    def readFields(self) -> None:
        self["notificationMessageId"] = self.getAtIndex(2)
        self["error"] = self.getAtIndex(3)


class DOP2HoursOfOperation(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 119]

    def readFields(self) -> None:
        prefix = "hoursOfOperation"
        suffixes = ["", "BeforeReplacement", "SinceLastMaintenance", "Mode1", "Mode2"]
        for count, suffix in enumerate(suffixes, start=1):
            if count in self.tree:
                self[prefix + suffix] = self.getAtIndex(count)


class DOP2PartName(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 173]

    def readFields(self) -> None:
        self["partName"] = self.getStringAtIndex(1)
        self["code"] = self.getStringAtIndex(2)


class DOP2DateOfTest(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 174]

    def readFields(self) -> None:
        self["partName"] = self.getStringAtIndex(1)
        self["dateOfTest"] = self.getStringAtIndex(2)


class DOP2SuperVisionListConfig(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [14, 1570]

    def readFields(self) -> None:
        self["isSuperVisionActive"] = self.getBoolAtIndex(1)
        self["isSuperVisionOnErrorOnly"] = self.getBoolAtIndex(2)
        self["isTimeMaster"] = self.getBoolAtIndex(3)
        self["listSize"] = self.getAtIndex(4)


class DOP2SuperVisionListItem(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [14, 1571]

    def readFields(self) -> None:
        self["deviceId"] = self.getAtIndex(1)
        self["deviceIdEnum"] = self.getAtIndex(2)
        self["deviceName"] = self.getStringAtIndex(3)
        self["connectionState"] = self.getAtIndex(4)


class DOP2DeviceState(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 256]

    def readFields(self) -> None:
        mapping = {
            1: ("mainState", self.getAtIndex),
            2: ("remoteEnable", self.getAtIndex),
            3: ("programType", self.getAtIndex),
            4: ("programId", self.getAtIndex),
            5: ("programPhase", self.getAtIndex),
            6: ("startTimeRelative", self.getAtIndex),
            7: ("remainingTime", self.getAtIndex),
            8: ("elapsedTimeRelative", self.getAtIndex),
            9: ("processTemperatureSet", self.getArrayToStrAtIndex),
            10: ("processTemperatureCurrent", self.getArrayToStrAtIndex),
            11: ("coreTemperatureSet", self.getArrayToStrAtIndex),
            12: ("coreTemperatureCurrent", self.getArrayToStrAtIndex),
            13: ("signalDoor", self.getAtIndex),
            14: ("signalInfo", self.getAtIndex),
            15: ("spinningSpeed", self.getAtIndex),
            16: ("dryingStep", self.getAtIndex),
            17: ("lightState", self.getAtIndex),
            18: ("standbyState", self.getAtIndex),
        }
        for idx, (key, getter) in mapping.items():
            if idx in self.tree:
                self[key] = getter(idx)


class DOP2ProcessData(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 6195]

    def readFields(self) -> None:
        fields = {
            4: "remainingTimeInMinutes",
            5: "programPhase",
            8: "heaterRelay",
            9: "lyePump",
            10: "circulationPump",
            11: "coldWaterValve",
            12: "hotWaterValve",
            15: "fuTemperature",
            16: "energyConsumed",
            17: "waterConsumedInLitres",
            27: "rpmCurrent",
        }
        for idx, name in fields.items():
            if idx in self.tree:
                self[name] = self.getValueWithInterpretationAtIndex(idx)


class DOP2DeviceIdent(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 144]

    def readFields(self) -> None:
        self["mieleDeviceId"] = self.getEnumAsStrAtIndex(1, DeviceType)
        self["protocolType"] = self.getEnumAsStrAtIndex(2, ProtocolType)
        if 3 in self.tree:
            self["supportedFunctions"] = self.getArrayToStrAtIndex(3)
        if 5 in self.tree:
            self["supportedApplications"] = self.getArrayToStrAtIndex(5)
        if 7 in self.tree:
            self["deviceUnits"] = self.getArrayToStrAtIndex(7)


class DOP2_SF_List(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 114]

    def readFields(self) -> None:
        self["validElementCount"] = self.getAtIndex(1)
        self["validElements"] = self.getEnumFlagsAtIndex(2, SfValueId)


class DOP2DateTime(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [14, 122]

    def readFields(self) -> None:
        self["utcTime"] = self.getAtIndex(1)
        self["offset"] = self.getAtIndex(2)


class DOP2ActuatorData(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 6192]

    def readFields(self) -> None:
        fields = [
            "heater1", "lyePump", "intensiveFlowPump", "valve1", "valve2",
            "waterDistributorMotor", "heater2", "twinDosPump1", "twinDosPump2",
            "steamHeater", "steamPump", "dosRel1", "dosRel2", "dosRel3",
            "dosRel4", "dosRel5", "dosRel6", "actCoinerEnd", "actCoinerOperation",
            "sensPeakLoad",
        ]
        for count, name in enumerate(fields, start=1):
            if count in self.tree:
                self[name] = self.getValueWithInterpretationAtIndex(count)


class DOP2SensorData(DOP2Annotator):
    """CDV_SensorData - Includes TwinDos container switches, water level, door lock."""

    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 6193]

    def readFields(self) -> None:
        fields = {
            1: "waterLevel",
            2: "waterInletWay",
            3: "spinSpeed",
            4: "doorSwitch",
            5: "doorLockSwitch",
            6: "wpsSwitch",
            7: "twinDosSwitchContainer1",
            8: "twinDosSwitchContainer2",
            9: "ntcTemperature1",
            10: "ntcTemperature2",
            11: "lanceContact",
            12: "peakLoadSignal",
            13: "detectedCap",
            14: "dispenserDrawerSwitch",
            15: "steamUnitTemperature",
            16: "sensCoinerPayment",
        }
        for idx, name in fields.items():
            if idx in self.tree:
                self[name] = self.getValueWithInterpretationAtIndex(idx)


class DOP2XKMConfigSSIDList(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [14, 110]

    def readFields(self) -> None:
        self["ssid"] = self.getStringAtIndex(1)
        self["wlanSecurity"] = self.getAtIndex(2)
        self["rssi"] = self.getAtIndex(3)
        self["wifiChannel"] = self.getAtIndex(4)


class DOP2SoftwareBuild(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [2, 6194]

    def readFields(self) -> None:
        self["date"] = self.getStringAtIndex(1)
        self["time"] = self.getStringAtIndex(2)
        self["id"] = self.getAtIndex(3)
        self["version"] = self.getAtIndex(4)


class DOP2SysObjectId(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [1, 19]

    def readFields(self) -> None:
        self["objectId"] = self.getAtIndex(1)
        self["instances"] = self.getAtIndex(2)
        self["authRead"] = self.getAtIndex(3)
        self["authWrite"] = self.getAtIndex(4)
        self["authSubscribe"] = self.getAtIndex(5)


class DOP2XKMState(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [14, 1568]

    def readFields(self) -> None:
        self["state"] = self.getAtIndex(1)
        self["signalQuality"] = self.getAtIndex(2)


class DOP2XKMIdent(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [14, 1565]

    def readFields(self) -> None:
        self["applicationType"] = self.getAtIndex(2)
        self["moduleType"] = self.getAtIndex(3)
        self["softwareVersion"] = self.getStringAtIndex(4)
        self["softwareId"] = self.getAtIndex(5)
        self["macAddressWifi"] = self.getStringAtIndex(6)
        self["applicationScope"] = self.getAtIndex(7)
        self["macAddressLan"] = self.getStringAtIndex(8)


class DOP2XKMIdentLabel(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [14, 1566]

    def readFields(self) -> None:
        self["serialNumber"] = self.getStringAtIndex(1)
        self["fabricationNumber"] = self.getStringAtIndex(2)
        self["technicalType"] = self.getStringAtIndex(3)
        self["materialNumber"] = self.getStringAtIndex(4)


class DOP2XKMConfigIP(DOP2Annotator):
    @staticmethod
    def getLeaf() -> List[int]:
        return [14, 1573]

    def readFields(self) -> None:
        self["ipAuto"] = self.getAtIndex(1)
        self["ipAddress"] = self.getIpAtIndex(2)
        self["subnetMask"] = self.getIpAtIndex(3)
        self["gatewayAddress"] = self.getIpAtIndex(4)
        self["dnsServerAuto"] = self.getAtIndex(5)
        self["dnsServer1"] = self.getIpAtIndex(6)
        self["dnsServer2"] = self.getIpAtIndex(7)
        self["wifiKey"] = self.getWifiKeyAtIndex(8)
        self["wifiSSID"] = self.getAtIndex(9)
        self["wifiSecurityType"] = self.getAtIndex(10)
        self["wifiChannel"] = self.getAtIndex(11)


DOP2Annotators = [
    DOP2_PS_Select,
    DOP2DeviceIdent,
    DOP2ProgramList,
    DOP2_SF_List,
    DOP2_SF_Value,
    DOP2LastUpdateInfo,
    DOP2UpdateControl,
    DOP2FileList,
    DOP2FileInfo,
    DOP2FileWrite,
    DOP2RSAPublicKey,
    DOP2PartName,
    DOP2DateOfTest,
    DOP2SuperVisionListItem,
    DOP2SuperVisionListConfig,
    DOP2ActuatorData,
    DOP2SensorData,
    DOP2ProcessData,
    DOP2DeviceState,
    DOP2SoftwareBuild,
    DOP2XKMState,
    DOP2NotificationShow,
    DOP2NotificationShowAlt,
    DOP2NotificationAcknowledge,
    DOP2XKMIdent,
    DOP2XKMIdentLabel,
    DOP2XKMConfigSSIDList,
    DOP2DateTime,
    DOP2DeviceContext,
    DOP2SecondDeviceContext,
    DOP2DeviceCombinedState,
    DOP2UserRequest,
    DOP2SoftwareIds,
    DOP2SysObjectId,
    DOP2XKMConfigIP,
    DOP2HoursOfOperation,
    ProgramInfoCA,
    ProgramStepInfoCA,
    ProgramName,
    ProgramInstructionsCA,
]
