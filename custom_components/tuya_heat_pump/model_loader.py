"""Model loader for Tuya Heat Pump."""
import logging
import importlib
from typing import Dict, Any
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Cache for loaded models
_MODEL_CACHE = {}

async def async_load_model_mapping(hass: HomeAssistant, model_id: str = None) -> Dict[str, Any]:
    """Load model mapping based on model ID - ASYNC VERSION."""
    # Default model ID if not provided
    if not model_id:
        model_id = "default"
    
    # Check cache first
    if model_id in _MODEL_CACHE:
        _LOGGER.debug("Using cached model mapping: %s", model_id)
        return _MODEL_CACHE[model_id]
    
    try:
        # Perform the import asynchronously
        def _import_model():
            try:
                # Try specific model first
                return importlib.import_module(f".models.{model_id}", __package__)
            except ImportError:
                # Fall back to default
                return importlib.import_module(".models.default", __package__)
        
        model_module = await hass.async_add_executor_job(_import_model)
        
        if model_module:
            # Create mapping dictionary
            mapping = {
                "sensors": getattr(model_module, "SENSOR_TYPES", {}),
                "binary_sensors": getattr(model_module, "BINARY_SENSOR_TYPES", {}),
                "switches": getattr(model_module, "SWITCH_TYPES", {}),
                "numbers": getattr(model_module, "NUMBER_TYPES", {}),
                "selects": getattr(model_module, "SELECT_TYPES", {}),
                "model_id": model_id,
                "model_name": getattr(model_module, "MODEL_NAME", f"Model {model_id}")
            }
            
            # Cache it
            _MODEL_CACHE[model_id] = mapping
            _LOGGER.info("✅ Model mapping loaded: %s", model_id)
            
            return mapping
            
    except Exception as err:
        _LOGGER.error("❌ Failed to load model %s: %s", model_id, err)
        return _create_empty_mapping(model_id)

def _create_empty_mapping(model_id: str) -> Dict[str, Any]:
    """Create empty mapping when no model file is found."""
    _LOGGER.warning("Creating empty mapping for model: %s", model_id)
    return {
        "sensors": {},
        "binary_sensors": {},
        "switches": {},
        "numbers": {},
        "selects": {},
        "model_id": model_id,
        "model_name": "Unknown Model"
    }

# ============================================================================
# SYNC VERSION FOR BACKWARD COMPATIBILITY (used by config_flow.py)
# ============================================================================

def load_model_mapping(model_id: str = None) -> Dict[str, Any]:
    """Sync version for backward compatibility (used by config_flow.py).
    
    NOTE: This is only used for config_flow validation.
    The async version must be used for real entities.
    """
    # Default model ID if not provided
    if not model_id:
        model_id = "default"
    
    try:
        # Try to load default module directly
        if model_id == "default":
            try:
                from .models.default import (
                    SENSOR_TYPES,
                    BINARY_SENSOR_TYPES,
                    SWITCH_TYPES,
                    NUMBER_TYPES,
                    SELECT_TYPES
                )
                
                mapping = {
                    "sensors": SENSOR_TYPES,
                    "binary_sensors": BINARY_SENSOR_TYPES,
                    "switches": SWITCH_TYPES,
                    "numbers": NUMBER_TYPES,
                    "selects": SELECT_TYPES,
                    "model_id": model_id,
                    "model_name": "Default Model"
                }
                
                _LOGGER.debug("Sync model mapping loaded: %s", model_id)
                return mapping
                
            except ImportError:
                # models/default.py not found
                pass
        
        # Other models or default not found
        try:
            module = importlib.import_module(f".models.{model_id}", __package__)
            
            mapping = {
                "sensors": getattr(module, "SENSOR_TYPES", {}),
                "binary_sensors": getattr(module, "BINARY_SENSOR_TYPES", {}),
                "switches": getattr(module, "SWITCH_TYPES", {}),
                "numbers": getattr(module, "NUMBER_TYPES", {}),
                "selects": getattr(module, "SELECT_TYPES", {}),
                "model_id": model_id,
                "model_name": getattr(module, "MODEL_NAME", f"Model {model_id}")
            }
            
            _LOGGER.debug("Sync model mapping loaded: %s", model_id)
            return mapping
            
        except ImportError:
            # Model not found, try default
            try:
                module = importlib.import_module(".models.default", __package__)
                
                mapping = {
                    "sensors": getattr(module, "SENSOR_TYPES", {}),
                    "binary_sensors": getattr(module, "BINARY_SENSOR_TYPES", {}),
                    "switches": getattr(module, "SWITCH_TYPES", {}),
                    "numbers": getattr(module, "NUMBER_TYPES", {}),
                    "selects": getattr(module, "SELECT_TYPES", {}),
                    "model_id": model_id,
                    "model_name": getattr(module, "MODEL_NAME", f"Model {model_id}")
                }
                
                _LOGGER.debug("Sync default mapping loaded for: %s", model_id)
                return mapping
                
            except ImportError:
                # Nothing found
                pass
                
    except Exception as err:
        _LOGGER.debug("Sync model loading failed: %s", err)
    
    # Return empty mapping as last resort
    _LOGGER.debug("Returning empty mapping for: %s", model_id)
    return {
        "sensors": {},
        "binary_sensors": {},
        "switches": {},
        "numbers": {},
        "selects": {},
        "model_id": model_id,
        "model_name": "Unknown Model"
    }
