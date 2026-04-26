"""Model mapping for 000000324z (Aquark Mr. Silence MS130)."""

MODEL_NAME = "Aquark Mr. Silence MS130 (000000324z)"
# ====================================================
# Aquark Mr. Silence MS130 @reitermarkus
# ====================================================
SENSOR_TYPES = {
    "water_inlet_temperature": {
        "dp_id": 102,
        "code": "WInTemp",
        "name": "Water Inlet Temperature",
        "unit": "°C",
        "icon": "mdi:thermometer",
        "device_class": "temperature",
        "state_class": "measurement",
        "conversion": "value",
    },
    "speed_percentage": {
        "dp_id": 104,
        "code": "SpeedPercentage",
        "name": "Speed",
        "unit": "%",
        "icon": "mdi:speedometer",
        "state_class": "measurement",
        "conversion": "value",
    },
    "lower_temperature_limit": {
        "dp_id": 107,
        "code": "SetDnLimit",
        "name": "Lower Temperature Limit",
        "unit": "°C",
        "icon": "mdi:thermometer-chevron-down",
        "device_class": "temperature",
        "state_class": "measurement",
        "conversion": "value",
    },
    "upper_temperature_limit": {
        "dp_id": 108,
        "code": "SetUpLimit",
        "name": "Upper Temperature Limit",
        "unit": "°C",
        "icon": "mdi:thermometer-chevron-up",
        "device_class": "temperature",
        "state_class": "measurement",
        "conversion": "value",
    },
    "fault_code_1": {
        "dp_id": 115,
        "code": "fault1",
        "name": "Fault Code 1",
        "icon": "mdi:alert-circle",
        "conversion": "'OK' if value == 0 else f'Code: {value}'",
    },
    "fault_code_2": {
        "dp_id": 116,
        "code": "fault2",
        "name": "Fault Code 2",
        "icon": "mdi:alert-circle",
        "conversion": "'OK' if value == 0 else f'Code: {value}'",
    },
}

# Binary Sensor Types - read-only bool/bitmap
BINARY_SENSOR_TYPES = {}

# Switch Types - read-write bool
SWITCH_TYPES = {
    # Power Switch
    "power": {
        "dp_id": 1,
        "code": "Power",
        "name": "Power",
        "icon": "mdi:power",
        "conversion": "value",
    },
}

# Number Types - read-write value
NUMBER_TYPES = {
    "target_temperature": {
        "dp_id": 106,
        "code": "SetTemp",
        "name": "Target Temperature",
        "icon": "mdi:thermometer",
        "unit": "°C",
        "min_value": 18.0,  # DP 107
        "max_value": 40.0,  # DP 108
        "step": 1.0,
        "conversion": "value",
        "api_conversion": "value",
    },
}

# Select Types - read-write enum
SELECT_TYPES = {
    "temperature_unit": {
        "dp_id": 104,
        "code": "change_tem",
        "name": "Temperature Unit",
        "icon": "mdi:thermometer",
        "options": {
            "false": "°C",
            "true": "°F",
        },
        "conversion": "value",
    },
    "mode": {
        "dp_id": 105,
        "code": "SetMode",
        "name": "Mode",
        "icon": "mdi:air-conditioner",
        "options": {
            "smart": "Auto",
            "warm": "Warm",
            "cool": "Cool",
        },
        "conversion": "value",
    },
    "silent_mode": {
        "dp_id": 117,
        "code": "SilentMdoe",
        "name": "Silent Mode",
        "icon": "mdi:sine-wave",
        "options": {
            "false": "Silent",
            "true": "Boost",
        },
        "conversion": "value",
    },
}
