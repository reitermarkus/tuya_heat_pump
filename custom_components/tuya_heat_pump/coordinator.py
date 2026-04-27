"""DataUpdateCoordinator for Tuya Heatpump."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import requests
import tinytuya
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    CONF_ACCESS_ID,
    CONF_ACCESS_KEY,
    CONF_CONNECTION_TYPE,
    CONF_DEVICE_ID,
    CONF_IP,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    DEFAULT_MANUFACTURER,
    DEFAULT_MODEL,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_COMMAND_PATH,
    DEVICE_DATA_PATH,
    DOMAIN,
    ERROR_AUTH,
    ERROR_CONN,
    REGIONS,
    TOKEN_PATH,
)
from .model_loader import async_load_model_mapping, load_model_mapping

_LOGGER = logging.getLogger(__name__)


def make_api_request(
    url: str, headers: dict, method: str = 'GET', data: dict = None
) -> requests.Response:
    """Make API request."""
    try:
        if method == 'POST':
            response = requests.post(url, headers=headers, json=data, timeout=10)
        else:
            response = requests.get(url, headers=headers, timeout=10)
        return response
    except requests.exceptions.Timeout:
        _LOGGER.error('Request timeout for %s', url)
        raise
    except requests.exceptions.RequestException as err:
        _LOGGER.error('Request error for %s: %s', url, err)
        raise


class TuyaScaleDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Tuya Heatpump data with Instant Updates."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.connection_type = config_entry.data.get(CONF_CONNECTION_TYPE, 'cloud')

        if self.connection_type == 'cloud':
            scan_interval = timedelta(
                minutes=config_entry.options.get(
                    CONF_SCAN_INTERVAL,
                    config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                )
            )
        else:
            scan_interval = None
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=scan_interval,
        )

        self.config_entry = config_entry
        self.device_id = config_entry.data[CONF_DEVICE_ID]
        self.device_name = DEFAULT_NAME
        self.is_online = True
        self._previous_online = True  # Track online status changes
        self.model_id = None
        self.model_mapping = None
        self.dp_mapping = {}
        self._listener_task = None
        self._heartbeat_task = None
        # Debounce (local mode)
        self._pending_commands = {}  # code → (value, task)
        self._debounce_delay = 1.0  # 1 second
        # Cache for last sent values (to prevent revert issues)
        self._sent_value_cache = {}  # code → (value, timestamp)
        self._cache_timeout = 8.0  # 8 seconds

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, self.device_id)},
            name=self.device_name,
            manufacturer=DEFAULT_MANUFACTURER,
            model=DEFAULT_MODEL,
        )

        # Cloud credentials - kept in both modes (required for model ID in local mode)
        self.access_id = config_entry.data.get(CONF_ACCESS_ID)
        self.access_key = config_entry.data.get(CONF_ACCESS_KEY)
        self.region = config_entry.data.get(CONF_REGION)
        self.api_endpoint = REGIONS.get(self.region)

        self.access_token = None

        if self.connection_type == 'cloud':
            # Nothing extra needed in cloud mode
            pass

        else:
            # Local mode: also store cloud credentials (required for model ID)
            self.ip = config_entry.data[CONF_IP]
            self.local_key = config_entry.data[CONF_LOCAL_KEY]
            self.protocol = float(config_entry.data.get(CONF_PROTOCOL, '3.4'))
            try:
                self.local_device = tinytuya.Device(
                    dev_id=self.device_id,
                    address=self.ip,
                    local_key=self.local_key,
                    version=self.protocol,
                    persist=True,
                )
                self.local_device.set_socketPersistent(True)
                self.local_device.set_socketNODELAY(True)
                _LOGGER.info(
                    'Local Tuya device initialized (Persistent Mode + NoDelay): %s',
                    self.device_id,
                )

                self.hass.loop.create_task(self._async_start_listener())

            except Exception as err:
                _LOGGER.error('Failed to initialize TinyTuya device: %s', err)
                self.local_device = None

    async def _async_start_listener(self):
        """Start the background listener for instant updates."""
        _LOGGER.info('Starting TinyTuya listener loop for %s', self.device_id)
        await self.async_refresh()
        self._listener_task = self.hass.loop.create_task(self._listen_loop())
        self._heartbeat_task = self.hass.loop.create_task(self._heartbeat_loop())

    async def _listen_loop(self):
        """Loop to receive instant data from the device."""
        while True:
            try:
                await asyncio.sleep(0.05)
                data = await self.hass.async_add_executor_job(self.local_device.receive)
                if data and 'dps' in data:
                    _LOGGER.debug('Instant update received: %s', data['dps'])
                    new_data = self._process_local_dps(data['dps'])
                    if new_data:
                        self._apply_sent_cache(new_data)
                        self.async_set_updated_data(new_data)
                await asyncio.sleep(0.1)
            except Exception as err:
                _LOGGER.error('Error in listener loop: %s. Retrying in 5s...', err)
                await asyncio.sleep(5)

    async def _heartbeat_loop(self):
        """Loop to keep the connection alive."""
        while True:
            try:
                if self.local_device:
                    await self.hass.async_add_executor_job(self.local_device.heartbeat)
                await asyncio.sleep(5)
            except Exception as err:
                _LOGGER.debug('Heartbeat error: %s', err)
                await asyncio.sleep(5)

    def _apply_sent_cache(self, new_data: dict):
        """If device returned an outdated value, force-apply the last sent value from cache."""
        current_time = time.time()
        for code, (sent_value, sent_time) in list(self._sent_value_cache.items()):
            if current_time - sent_time > self._cache_timeout:
                del self._sent_value_cache[code]
                continue

            if code in new_data and new_data[code]['value'] != sent_value:
                _LOGGER.warning(
                    "Device returned stale value (%s = %s), correcting from cache → %s",
                    code,
                    new_data[code]['value'],
                    sent_value,
                )
                new_data[code]['value'] = sent_value
                new_data[code]['timestamp'] = int(time.time() * 1000)

    def _process_local_dps(self, dps: dict) -> dict:
        """Helper to convert raw DPS to our data format."""
        data = {}
        current_ms = int(time.time() * 1000)
        current_str = datetime.fromtimestamp(current_ms / 1000).strftime(
            '%Y-%m-%d %H:%M:%S'
        )

        for dp_str, value in dps.items():
            try:
                dp_id = int(dp_str)
                code = self.dp_mapping.get(dp_id)
                if code:
                    data[code] = {
                        'value': value,
                        'timestamp': current_ms,
                        'type': str(type(value).__name__),
                        'last_update': current_str,
                    }
            except ValueError:
                continue

        if self.data and isinstance(self.data, dict):
            updated_data = dict(self.data)
            updated_data.update(data)
            return updated_data

        return data

    def _calculate_sign(
        self,
        t: str,
        path: str,
        access_token: str = None,
        method: str = 'GET',
        body: str = '',
    ) -> str:
        """Calculate signature for API requests."""
        str_to_sign = []
        str_to_sign.append(method)
        str_to_sign.append(
            hashlib.sha256(
                body.encode('utf8') if body else ''.encode('utf8')
            ).hexdigest()
        )
        str_to_sign.append('')
        str_to_sign.append(path)
        str_to_sign = '\n'.join(str_to_sign)

        message = self.access_id
        if access_token:
            message += access_token
        message += t + str_to_sign

        signature = (
            hmac.new(
                self.access_key.encode('utf-8'), message.encode('utf-8'), hashlib.sha256
            )
            .hexdigest()
            .upper()
        )

        return signature

    async def _get_token(self) -> bool:
        """Get access token from Tuya API - used for both cloud and local modes."""
        if self.access_token:
            _LOGGER.debug('Token already available, skipping re-fetch')
            return True

        try:
            t = str(int(time.time() * 1000))
            sign = self._calculate_sign(t, TOKEN_PATH)

            headers = {
                'client_id': self.access_id,
                'sign': sign,
                't': t,
                'sign_method': 'HMAC-SHA256',
            }

            url = f'{self.api_endpoint}{TOKEN_PATH}'

            response = await self.hass.async_add_executor_job(
                make_api_request, url, headers
            )

            if response.status_code != 200:
                _LOGGER.error('Token endpoint returned HTTP %s', response.status_code)
                raise ConfigEntryAuthFailed(ERROR_AUTH)

            result = response.json()
            if not result.get('success', False):
                error_msg = result.get('msg', 'Unknown error')
                _LOGGER.error('Failed to obtain token: %s', error_msg)
                raise ConfigEntryAuthFailed(f'{ERROR_AUTH}: {error_msg}')

            self.access_token = result['result']['access_token']
            _LOGGER.info('Access token obtained successfully')
            return True

        except Exception as err:
            _LOGGER.error('Error obtaining token: %s', str(err))
            raise UpdateFailed(f'{ERROR_CONN}: {str(err)}')

    async def get_device_info(self) -> dict:
        """Get device information."""
        if self.connection_type == 'cloud':
            try:
                if not self.access_token:
                    await self._get_token()

                t = str(int(time.time() * 1000))
                path = f'/v1.0/devices/{self.device_id}'
                sign = self._calculate_sign(t, path, self.access_token)

                headers = {
                    'client_id': self.access_id,
                    'access_token': self.access_token,
                    'sign': sign,
                    't': t,
                    'sign_method': 'HMAC-SHA256',
                }

                url = f'{self.api_endpoint}{path}'
                _LOGGER.info('Getting device info from API...')

                response = await self.hass.async_add_executor_job(
                    make_api_request, url, headers
                )

                result = response.json()

                if result.get('success', False):
                    device_data = result['result']
                    self.device_name = device_data.get('name', DEFAULT_NAME)
                    product_name = device_data.get('product_name', DEFAULT_MODEL)

                    _LOGGER.info('Device name set to: %s', self.device_name)

                    self.device_info = DeviceInfo(
                        identifiers={(DOMAIN, self.device_id)},
                        name=self.device_name,
                        manufacturer=DEFAULT_MANUFACTURER,
                        model=product_name,
                    )
                    return device_data
                else:
                    _LOGGER.warning(
                        'Failed to get device info, using default name: %s',
                        DEFAULT_NAME,
                    )
                    return {}

            except Exception as err:
                _LOGGER.error('Error getting device info: %s', str(err))
                return {}
        else:
            self.device_name = f'Tuya Heat Pump (Local) {self.device_id[-6:]}'
            self.device_info = DeviceInfo(
                identifiers={(DOMAIN, self.device_id)},
                name=self.device_name,
                manufacturer=DEFAULT_MANUFACTURER,
                model='Local Device',
            )
            return {}

    async def get_device_model(self) -> dict:
        """Get device model information - cloud API is also used in local mode."""
        _LOGGER.info(
            'get_device_model called - connection_type: %s', self.connection_type
        )

        try:
            if not self.access_token:
                _LOGGER.info('No token → fetching token...')
                await self._get_token()

            t = str(int(time.time() * 1000))
            path = f'/v2.0/cloud/thing/{self.device_id}/model'
            sign = self._calculate_sign(t, path, self.access_token)

            headers = {
                'client_id': self.access_id,
                'access_token': self.access_token,
                'sign': sign,
                't': t,
                'sign_method': 'HMAC-SHA256',
            }

            url = f'{self.api_endpoint}{path}'
            _LOGGER.info('Fetching model info from Cloud API: %s', url)

            response = await self.hass.async_add_executor_job(
                make_api_request, url, headers
            )

            result = response.json()

            if result.get('success', False):
                model_info = json.loads(result['result']['model'])
                self.model_id = model_info.get('modelId')
                _LOGGER.info('Model ID: %s', self.model_id)

                self.model_mapping = await async_load_model_mapping(
                    self.hass, self.model_id
                )

                self.dp_mapping = {}
                for entity_type in [
                    'sensors',
                    'binary_sensors',
                    'switches',
                    'numbers',
                    'selects',
                ]:
                    for code, config in self.model_mapping.get(entity_type, {}).items():
                        if 'dp_id' in config:
                            dp_id = config['dp_id']
                            self.dp_mapping[dp_id] = code

                _LOGGER.info(
                    'Model mapping loaded - %d DPs defined', len(self.dp_mapping)
                )
                return model_info
            else:
                _LOGGER.warning(
                    'Model API success=false → using default. Msg: %s',
                    result.get('msg', '—'),
                )
                self.model_id = 'default'

        except Exception as err:
            _LOGGER.warning(
                'Failed to get model info: %s → will use default mapping', str(err)
            )
            self.model_id = 'default'

        # Default fallback
        self.model_mapping = load_model_mapping('default')
        self.dp_mapping = {}
        for entity_type in [
            'sensors',
            'binary_sensors',
            'switches',
            'numbers',
            'selects',
        ]:
            for code, config in self.model_mapping.get(entity_type, {}).items():
                if 'dp_id' in config:
                    dp_id = config['dp_id']
                    self.dp_mapping[dp_id] = code

        _LOGGER.info(
            'Default model mapping loaded - %d DP(s) defined', len(self.dp_mapping)
        )
        return {}

    async def send_command(self, code: str, value: Any) -> bool:
        """Send command to device - uses debounce to send the latest value in local mode."""
        try:
            if self.connection_type == 'cloud':
                if not self.access_token:
                    await self._get_token()
                t = str(int(time.time() * 1000))
                path = DEVICE_COMMAND_PATH.format(device_id=self.device_id)

                _LOGGER.debug('Cloud → sending raw value: %s', value)
                properties = {code: value}
                properties_json = json.dumps(properties)
                body_dict = {'properties': properties_json}

                body_str = json.dumps(body_dict)
                sign = self._calculate_sign(
                    t, path, self.access_token, 'POST', body_str
                )

                headers = {
                    'client_id': self.access_id,
                    'access_token': self.access_token,
                    'sign': sign,
                    't': t,
                    'sign_method': 'HMAC-SHA256',
                    'Content-Type': 'application/json',
                }

                url = f'{self.api_endpoint}{path}'
                _LOGGER.info('Cloud command (v2.0) - raw value: %s = %s', code, value)

                response = await self.hass.async_add_executor_job(
                    make_api_request, url, headers, 'POST', body_dict
                )

                result = response.json()

                if result.get('success', False):
                    _LOGGER.info('✅ Cloud command successful: %s = %s', code, value)
                    await asyncio.sleep(2)
                    await self.async_request_refresh()
                    return True
                else:
                    error_msg = result.get('msg', 'Unknown error')
                    _LOGGER.error(
                        '❌ Cloud command failed: %s = %s → %s', code, value, error_msg
                    )
                    return False

            else:  # Local mod - DEBOUNCE
                if not self.local_device:
                    _LOGGER.error('Local device not initialized')
                    return False

                dp_id = next((k for k, v in self.dp_mapping.items() if v == code), None)
                if dp_id is None:
                    _LOGGER.error('No dp_id mapping found for code: %s', code)
                    return False

                # Cancel any pending task for this code
                if code in self._pending_commands:
                    task = self._pending_commands[code][1]
                    task.cancel()
                    _LOGGER.debug('Previous debounce cancelled: %s', code)

                # Write last sent value to cache
                self._sent_value_cache[code] = (value, time.time())

                # Create new debounce task
                async def delayed_send():
                    await asyncio.sleep(self._debounce_delay)
                    try:
                        result = await self.hass.async_add_executor_job(
                            self.local_device.set_value, dp_id, value
                        )
                        if result:
                            _LOGGER.info(
                                '✅ Debounce send successful: dp %s (%s) = %s',
                                dp_id,
                                code,
                                value,
                            )
                        else:
                            _LOGGER.warning(
                                '❌ Debounce send failed: dp %s', dp_id
                            )
                    except Exception as err:
                        _LOGGER.error(
                            'Debounce send error %s = %s: %s', code, value, err
                        )
                    finally:
                        if code in self._pending_commands:
                            del self._pending_commands[code]

                task = self.hass.loop.create_task(delayed_send())
                self._pending_commands[code] = (value, task)

                _LOGGER.info(
                    'Local command queued for debounce: dp %s (%s) = %s (sending in %.1f s)',
                    dp_id,
                    code,
                    value,
                    self._debounce_delay,
                )

                # Return success to user immediately
                return True

        except Exception as err:
            _LOGGER.error('Error sending command %s: %s', code, str(err))
            return False

    async def _async_update_data(self):
        """Fetch data from Tuya API or local device (Manual Poll)."""
        if self.connection_type == 'cloud':
            try:
                if not self.access_token:
                    await self._get_token()
                t = str(int(time.time() * 1000))
                path = DEVICE_DATA_PATH.format(device_id=self.device_id)
                sign = self._calculate_sign(t, path, self.access_token)

                headers = {
                    'client_id': self.access_id,
                    'access_token': self.access_token,
                    'sign': sign,
                    't': t,
                    'sign_method': 'HMAC-SHA256',
                }

                url = f'{self.api_endpoint}{path}'

                response = await self.hass.async_add_executor_job(
                    make_api_request, url, headers
                )

                if response.status_code == 401:
                    _LOGGER.warning('401 Unauthorized - refreshing token')
                    self.access_token = None
                    return await self._async_update_data()

                if response.status_code != 200:
                    self.is_online = False
                    _LOGGER.info(
                        'Online status changed: OFFLINE (HTTP %s)', response.status_code
                    )
                    self.async_update_listeners()
                    raise UpdateFailed(f'HTTP error {response.status_code}')

                result = response.json()
                if not result.get('success', False):
                    msg = result.get('msg', '')
                    if 'token' in msg.lower():
                        self.access_token = None
                        return await self._async_update_data()
                    self.is_online = False
                    _LOGGER.info('Online status changed: OFFLINE (API error: %s)', msg)
                    self.async_update_listeners()
                    raise UpdateFailed(f'API error: {msg}')

                # NEW: Legacy logic - online check via timestamp
                current_time = int(time.time() * 1000)
                properties = result.get('result', {}).get('properties', [])

                if properties:
                    latest_timestamp = max(prop.get('time', 0) for prop in properties)
                    time_diff = current_time - latest_timestamp

                    scan_interval_ms = (
                        self.update_interval.total_seconds() * 1000
                        if self.update_interval
                        else 180000
                    )
                    tolerance_ms = scan_interval_ms + (60 * 1000)  # +1 dakika

                    if time_diff > tolerance_ms:
                        self.is_online = False
                        _LOGGER.info(
                            'Device OFFLINE - data %s seconds old', time_diff // 1000
                        )
                    else:
                        self.is_online = True
                        _LOGGER.debug(
                            'Device ONLINE - fresh data (%s seconds old)',
                            time_diff // 1000,
                        )
                else:
                    self.is_online = False
                    _LOGGER.info('Device OFFLINE - no properties')

                if self._previous_online != self.is_online:
                    _LOGGER.info(
                        'Online status changed: %s',
                        'ONLINE' if self.is_online else 'OFFLINE',
                    )
                    self._previous_online = self.is_online

                self.async_update_listeners()  # Update binary sensor

                # Process data
                data = {}
                for prop in properties:
                    code = prop['code']
                    data[code] = {
                        'value': prop['value'],
                        'timestamp': prop.get('time', 0),
                        'type': prop.get('type', ''),
                        'last_update': datetime.fromtimestamp(
                            prop.get('time', 0) / 1000
                        ).strftime('%Y-%m-%d %H:%M:%S'),
                    }
                return data

            except Exception as err:
                self.is_online = False
                if self._previous_online != self.is_online:
                    _LOGGER.info('Online status changed: OFFLINE (exception: %s)', err)
                    self._previous_online = self.is_online
                self.async_update_listeners()
                raise UpdateFailed(f'Error: {str(err)}')

        else:
            if not self.local_device:
                raise UpdateFailed('Local device not initialized')

            try:
                status = await self.hass.async_add_executor_job(
                    self.local_device.status
                )

                if not status or 'dps' not in status:
                    _LOGGER.warning("No 'dps' in status response - retrying once")
                    await asyncio.sleep(1.0)
                    status = await self.hass.async_add_executor_job(
                        self.local_device.status
                    )

                    if not status or 'dps' not in status:
                        self.is_online = False
                        _LOGGER.info(
                            'Online status changed: OFFLINE (local status failed)'
                        )
                        self.async_update_listeners()
                        raise UpdateFailed(
                            "No 'dps' in local status response after retry"
                        )

                self.is_online = True
                if self._previous_online != self.is_online:
                    _LOGGER.info('Online status changed: ONLINE')
                    self._previous_online = self.is_online

                self.async_update_listeners()

                data = self._process_local_dps(status['dps'])
                self._apply_sent_cache(data)
                return data

            except Exception as err:
                self.is_online = False
                if self._previous_online != self.is_online:
                    _LOGGER.info(
                        'Online status changed: OFFLINE (local exception: %s)', err
                    )
                    self._previous_online = self.is_online
                self.async_update_listeners()
                raise UpdateFailed(f'Local error: {str(err)}')
