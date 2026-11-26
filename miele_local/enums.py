"""Miele@home protocol enums and device-type-aware decoders."""

from enum import Enum, IntEnum
from typing import Dict, Optional


class DeviceType(IntEnum):
    """Miele appliance device types (aligned with DOP2 GLOBAL_EnumMieleDeviceId)."""

    NoUse = 0
    WashingMachine = 1
    TumbleDryer = 2
    WashingMachineSemiPro = 3
    TumbleDryerSemiPro = 4
    WashingMachinePro = 5
    TumbleDryerPro = 6
    Dishwasher = 7
    DishwasherSemiPro = 8
    DishwasherPro = 9
    Range = 10
    RangeWithMicrowave = 11
    Oven = 12
    OvenWithMicrowave = 13
    Cooktop = 14
    SteamOven = 15
    Microwave = 16
    CoffeeMaker = 17
    Hood = 18
    Fridge = 19
    Freezer = 20
    FridgeWithFreezer = 21
    ChestFreezer = 22
    RobotVacuum = 23
    WasherDryer = 24
    WarmingDrawer = 25
    BeverageMaker = 26
    HobInduction = 27
    HobGas = 28
    HobHighlightInductionCombi = 29
    HobHighlightGasCombi = 30
    SteamOvenCombi = 31
    WineCabinet = 32
    WineConditioningUnit = 33
    WineStorageConditioningUnit = 34
    WaterDispenser = 35
    IceMaker = 36
    IceCrusher = 37
    CellarCompartment = 38
    OvenDouble = 39
    SteamOvenDouble = 40
    SteamOvenDoubleCombi = 41
    MicrowaveDouble = 42
    OvenMicrowaveDoubleCombi = 43
    WasherDisinfector = 44
    SteamOvenMicrowaveCombi = 45
    RemoteControl = 46
    VoiceControl = 47
    VacuumDrawer = 48
    CoolingUnit = 77
    LaundryCareCabinet = 79


class Status(IntEnum):
    """Miele device status codes."""

    NoUse = 0
    Off = 1
    On = 2
    Programmed = 3
    WaitingToStart = 4
    Running = 5
    Paused = 6
    EndedSuccessfully = 7
    Failure = 8
    Abort = 9
    Idle = 10
    Rinse = 11
    Service = 12
    SuperFreeze = 13
    SuperCool = 14
    SuperHeat = 15
    Default = 144
    Lock = 145
    SuperCoolSuperFreeze = 146


class DryingStep(IntEnum):
    ExtraDry = 0
    NormalPlus = 1
    Normal = 2
    SlightlyDry = 3
    HandIron1 = 4
    HandIron2 = 5
    MachineIron = 6
    HygieneDry = 7


class Light(IntEnum):
    NotSupported = 0
    Enabled = 1
    Disabled = 2


class RemoteControl(IntEnum):
    Disabled = 0
    EnabledButNotPossible = 7
    Full = 15
    Unknown = 2147483647


class ProtocolType(IntEnum):
    Unknown = 0
    Uart = 1
    MeterbusDop1 = 2
    MeterbusDop2 = 3
    SenseWireDop2 = 4


class OperationState(IntEnum):
    Unknown = 0
    EndOfLine = 1
    Service = 2
    Settings = 3
    InitialSettings = 4
    SelectProgram = 5
    RunProgram = 6
    RunDelay = 7
    RunMaintenanceProcess = 8
    VoltageBrownout = 9
    WelcomeScreen = 10
    Locked = 11
    TimeSettingScreen = 12
    DisplayOff = 15
    ColdRising = 21
    NormalRinsing = 22
    EmergencyStop = 32


class ProcessState(IntEnum):
    Unknown = 0
    NoProgram = 1
    ProgramSelected = 2
    ProgramStarted = 3
    ProgramRunning = 4
    ProgramStop = 5


class ApplianceState(IntEnum):
    Unknown = 0
    Off = 1
    Synchronizing = 2
    Initializing = 3
    Normal = 4
    Demonstration = 5
    Service = 6
    Error = 7
    Check = 8
    Standby = 9
    Supervisory = 10
    ShowWindow = 11


class ProgramType(IntEnum):
    BuiltInFunction = 1
    UserDefined = 2
    Automatic = 3
    CleaningProgram = 4
    CustomerService = 5
    Helper = 6


class UserRequest(IntEnum):
    NoRequest = 0
    Start = 1
    SetInteriorLightOn = 12141
    SetInteriorLightOff = 12142


class UserRequestOven(IntEnum):
    Nop = 0
    Start = 1
    Stop = 2
    Pause = 3
    StartDelay = 8
    DoorOpen = 11
    DoorClose = 12
    LightOn = 13
    LightOff = 14
    FactorySettingReset = 15
    SwitchOn = 16
    Next = 17
    Back = 18
    SwitchOff = 19
    ResetPinCode = 20
    KeepAlive = 21
    Step = 22
    StartRemoteUpdateInstall = 23
    ProgramStop = 54
    ProgramAbort = 55
    ProgramFinalize = 56
    ProgramSave = 61
    MotorizedFrontPanelOpen = 65
    MotorizedFrontPanelClose = 66
    HoldingBreak = 68
    HoldingStart = 69


class ProgramIdLaundry(IntEnum):
    """Washing machine / Dryer / WasherDryer program IDs (incl. WWD660 #7 & HandWash #24)."""

    NotSelected = 0
    CottonOrAutomatic = 1
    WhitesCottons = 2
    MinimumIron = 3
    Delicates = 4
    Delicate = 5
    HotAir = 6
    ColdAir = 7
    Wool = 8
    Cotton = 9
    Gentle = 10
    CottonHygiene = 11
    DrainSpin = 21
    Shirts = 23
    Waterproofing = 27
    Cottons25Celsius = 28
    MinimumIron25Celsius = 29
    SyntheticBedding = 30
    NaturalBedding = 31
    Microfiber = 32
    WetcareIntensive = 33
    WetcareSensitive = 34
    WetcareSilk = 35
    LargeItems = 36
    Reactivate = 37
    Smoothing = 38
    CottonsWhiteHygiene = 39
    Maintenance = 48
    ProgramFortyCelsius = 59
    ExpressTwentyMinutes = 122
    DarkGarmentsDenim = 123
    HandWash = 178
    EuropeanUnionEcoMode = 190


class ProgramIdOven(IntEnum):
    """Oven / SteamOven / Microwave program IDs (fixes Issue #27 collision with laundry IDs)."""

    NoProgram = 0
    DefrostBottom = 1
    AutoHotAir = 2
    AutoTopBottomHeat = 3
    EcoHotAir1 = 4
    EcoHotAir2 = 5
    EcoHotAir = 6
    RoastAutomatic = 7
    UniversalCook = 8
    Grill = 9
    GrillLarge = 10
    GrillSmall = 11
    Descale = 12
    HotAirPlus = 13
    IntenseBaking = 14
    ComboCookGrill = 15
    ComboCookHotAirPlus = 16
    ComboCookTopBottom = 17
    CakeSpecial = 18
    Microwave = 19
    MicrowaveRoastAutomatic = 20
    MicrowaveGrill = 21
    MicrowaveHotAirPlus = 22
    MicrowaveFanGrill = 23
    TopBottomHeat = 24
    TopHeat = 25
    Pyrolysis = 26
    RapidHeat = 27
    Rinse = 28
    FanGrill = 29
    EcoHotAir3 = 30
    BottomHeat = 31
    KeepWarm = 32
    DefrostSteam = 33
    DefrostMicrowave = 34
    ClimateRoastAutomatic = 35
    ConvectionBake = 36
    ConvectionRoast = 37
    MicrowaveConvectionBake = 38
    MicrowaveConvectionRoast = 39
    ClimateHotAirPlus = 40
    HeatBottom = 41
    HeatSteam = 42
    HeatMicrowave = 43
    CookFish = 44
    CookMeat = 45
    CookVegetables = 46
    ClimateConvectionBake = 47
    ClimateRoast = 48
    ClimateHotAirPlus2 = 49
    ClimateIntenseBaking = 50
    ClimateTopBottomHeat = 51
    ClimateConvectionRoast = 52
    Popcorn = 53
    QuickMicrowave = 54
    Rotisserie = 71
    SousVide = 72
    SteamDry = 73
    EcoUniversalCook = 75


class ProgramIdDishwasher(IntEnum):
    """Dishwasher program IDs."""

    NoProgram = 0
    Intensive75 = 1
    Normal60 = 2
    Automatic = 3
    Gentle = 4
    QuickPowerWash = 6
    Eco = 7
    Maintenance = 9
    ExtraQuiet = 10
    Intensive = 11
    PastaPaella = 12
    SolarSave = 13
    Hygiene = 14
    TallItems65 = 15
    GlassesWarm = 16
    PowerWash60 = 26
    Eco50 = 27
    SensorWash = 28
    Short = 30
    Quick40 = 32
    ComfortWash = 34
    ComfortWashPlus = 38


class ProgramIdCoffeeMaker(IntEnum):
    """Coffee maker program IDs."""

    NoProgram = 0
    Ristretto = 1
    Espresso = 2
    Coffee = 3
    LongCoffee = 4
    Cappuccino = 5
    LatteMacchiato = 6
    HotMilk = 7
    MilkFoam = 8
    HotWater = 9
    CaffeLatte = 10
    EspressoMacchiato = 11
    FlatWhite = 12
    CoffeePot = 13
    BlackTea = 14
    HerbalTea = 15
    FruitTea = 16
    GreenTea = 17
    WhiteTea = 18
    JapaneseTea = 19
    ApplianceRinse = 21
    MilkPipeworkRinse = 22
    MilkPipeworkClean = 23
    BrewUnitDegrease = 24
    Descaling = 25


class ProgramPhase(IntEnum):
    NotUsed = 0
    Progress = 1
    BatteryCharging = 2
    WashingMachineIdle = 256
    WashingMachinePreWash = 257
    WashingMachineSoak = 258
    WashingMachinePreRinse = 259
    WashingMachineWashing = 260
    WashingMachineRinse = 261
    WashingMachineRinseHold = 262
    WashingMachineClean = 263
    WashingMachineCooldown = 264
    WashingMachineDrain = 265
    WashingMachineSpin = 266
    WashingMachineAntiCrease = 267
    WashingMachineFinished = 268
    WashingMachineVenting = 269
    WashingMachineStarch = 270
    WashingMachineMoisting = 271
    WashingMachineRemoisting = 272
    WashingMachineHygiene = 279
    WashingMachineDrying = 280
    WashingMachineDisinfection = 285
    WashingMachineSteamSmoothing = 295
    TumbleDryerIdle = 512
    TumbleDryerProgramStart = 513
    TumbleDryerDrying = 514
    TumbleDryerMachineIron = 515
    TumbleDryerHandIron2 = 516
    TumbleDryerNormal = 517
    TumbleDryerNormalPlus = 518
    TumbleDryerComfortCooldown = 519
    TumbleDryerHandIron1 = 520
    TumbleDryerAntiCreaseFinish = 521
    TumbleDryerFinished = 522
    TumbleDryerExtraDry = 523
    TumbleDryerHandIronWithoutDrop = 524
    TumbleDryerHygieneDrying = 525
    TumbleDryerWetting = 526
    TumbleDryerSpin = 527
    TumbleDryerFanActive = 528
    TumbleDryerHotAir = 529
    TumbleDryerSteamSmooth = 530
    TumbleDryerCooling = 531
    TumbleDryerFluff = 532
    TumbleDryerRinse = 533
    TumbleDryerSmooth = 534
    TumbleDryerRemoteStart = 536
    TumbleDryerDelayedRun = 537
    TumbleDryerSlightlyDry = 538
    TumbleDryerSafetyCooldown = 539
    DishwasherNoProgram = 1792
    DishwasherRegenerate = 1793
    DishwasherPreRinse = 1794
    DishwasherClean = 1795
    DishwasherRinse = 1796
    DishwasherRinseInterim = 1797
    DishwasherRinseClear = 1798
    DishwasherDrying = 1799
    DishwasherFinished = 1800
    DishwasherPreRinse2 = 1801
    OvenCooldown = 3072
    OvenHeating = 3073
    OvenTempHold = 3074
    OvenDoorOpen = 3075
    OvenPyrolyze = 3076
    OvenMicrowave = 3077
    OvenProgramDone = 3078
    OvenLight = 3079
    OvenSearing = 3080
    OvenRoasting = 3081
    OvenDefrost = 3082
    OvenCooldown2 = 3083
    OvenEnergySave = 3084
    OvenHoldWarm = 3094
    OvenDescale = 3098
    OvenPreheat = 3099


class SfValueId(IntEnum):
    NONE = 0
    OvenTimeDisplay = 3
    OvenTimePresentation = 4
    OvenTimeFormat = 5
    OvenStartScreen = 10
    OvenDisplayBrightness = 11
    OvenDisplayColorScheme = 12
    OvenVolumeSignalLevel = 14
    OvenVolumeKeyTone = 17
    OvenVolumeWelcomeMelody = 18
    OvenLighting = 21
    OvenTemperatureUnit = 22
    OvenWeightUnit = 23
    OvenSafetyKeyLock = 24
    OvenSafetyStartupLock = 25
    OvenRemoteControl = 30
    OvenSupervisionFunction = 31
    OvenRemoteUpdate = 33
    OvenWaterHardness = 1021
    Global_Language = 10000
    Global_DisplayBrightness = 10001
    Global_WaterHardness = 10010
    Washer_DetergentTypeContainerOne = 12005
    Washer_DetergentAmountContainerOne = 12006
    Washer_DetergentTypeContainerTwo = 12007
    Washer_DetergentAmountContainerTwo = 12008
    Washer_RemoteControl = 12039
    Washer_AutoDispensing = 12046
    Washer_SmartGrid = 12061
    Dryer_DryingLevels = 16016


OVEN_DEVICE_TYPES = {
    DeviceType.Range,
    DeviceType.RangeWithMicrowave,
    DeviceType.Oven,
    DeviceType.OvenWithMicrowave,
    DeviceType.SteamOven,
    DeviceType.Microwave,
    DeviceType.SteamOvenCombi,
    DeviceType.OvenDouble,
    DeviceType.SteamOvenDouble,
    DeviceType.SteamOvenDoubleCombi,
    DeviceType.MicrowaveDouble,
    DeviceType.OvenMicrowaveDoubleCombi,
    DeviceType.SteamOvenMicrowaveCombi,
}

DISHWASHER_DEVICE_TYPES = {
    DeviceType.Dishwasher,
    DeviceType.DishwasherSemiPro,
    DeviceType.DishwasherPro,
}

COFFEE_DEVICE_TYPES = {
    DeviceType.CoffeeMaker,
    DeviceType.BeverageMaker,
}


def decode_enum(enum_cls: type[Enum], value: Optional[int], default: str = "Unknown") -> str:
    """Safely decode an integer value using an Enum class."""
    if value is None:
        return default
    try:
        return enum_cls(int(value)).name
    except (ValueError, TypeError):
        return f"Unknown({value})"


def decode_program_id(program_id: Optional[int], device_type: Optional[int] = None) -> str:
    """Decode ProgramID using the appliance's DeviceType to avoid cross-appliance ID collisions."""
    if program_id is None:
        return "Unknown"
    if device_type in OVEN_DEVICE_TYPES:
        return decode_enum(ProgramIdOven, program_id)
    if device_type in DISHWASHER_DEVICE_TYPES:
        return decode_enum(ProgramIdDishwasher, program_id)
    if device_type in COFFEE_DEVICE_TYPES:
        return decode_enum(ProgramIdCoffeeMaker, program_id)
    return decode_enum(ProgramIdLaundry, program_id)


def get_device_category(device_type: Optional[int]) -> str:
    """Map numeric DeviceType to a UI/API category slug."""
    mapping: Dict[int, str] = {
        DeviceType.WashingMachine: "washer",
        DeviceType.WashingMachineSemiPro: "washer",
        DeviceType.WashingMachinePro: "washer",
        DeviceType.TumbleDryer: "dryer",
        DeviceType.TumbleDryerSemiPro: "dryer",
        DeviceType.TumbleDryerPro: "dryer",
        DeviceType.WasherDryer: "washer_dryer",
        DeviceType.Dishwasher: "dishwasher",
        DeviceType.DishwasherSemiPro: "dishwasher",
        DeviceType.DishwasherPro: "dishwasher",
        DeviceType.Oven: "oven",
        DeviceType.OvenWithMicrowave: "oven",
        DeviceType.SteamOven: "oven",
        DeviceType.SteamOvenCombi: "oven",
        DeviceType.OvenDouble: "oven",
        DeviceType.SteamOvenDouble: "oven",
        DeviceType.SteamOvenDoubleCombi: "oven",
        DeviceType.OvenMicrowaveDoubleCombi: "oven",
        DeviceType.SteamOvenMicrowaveCombi: "oven",
        DeviceType.Range: "oven",
        DeviceType.RangeWithMicrowave: "oven",
        DeviceType.Microwave: "microwave",
        DeviceType.MicrowaveDouble: "microwave",
        DeviceType.Cooktop: "cooktop",
        DeviceType.HobInduction: "cooktop",
        DeviceType.HobGas: "cooktop",
        DeviceType.Hood: "hood",
        DeviceType.CoffeeMaker: "coffee",
        DeviceType.BeverageMaker: "coffee",
        DeviceType.Fridge: "fridge",
        DeviceType.Freezer: "freezer",
        DeviceType.FridgeWithFreezer: "fridge_freezer",
        DeviceType.ChestFreezer: "freezer",
        DeviceType.WineCabinet: "wine",
        DeviceType.WineConditioningUnit: "wine",
        DeviceType.WineStorageConditioningUnit: "wine",
        DeviceType.RobotVacuum: "vacuum",
        DeviceType.WarmingDrawer: "warming_drawer",
    }
    if device_type is None:
        return "appliance"
    return mapping.get(int(device_type), "appliance")
