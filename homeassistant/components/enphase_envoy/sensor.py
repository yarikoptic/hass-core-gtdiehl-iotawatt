from datetime import timedelta
import logging

import async_timeout

from envoy_reader.envoy_reader import EnvoyReader
import httpcore
import requests
import voluptuous as vol

from homeassistant.helpers import entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_IP_ADDRESS,
    CONF_MONITORED_CONDITIONS,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
    ENERGY_WATT_HOUR,
    POWER_WATT,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)

SENSORS = {
    "production": ("Envoy Current Energy Production", POWER_WATT),
    "daily_production": ("Envoy Today's Energy Production", ENERGY_WATT_HOUR),
    "seven_days_production": (
        "Envoy Last Seven Days Energy Production",
        ENERGY_WATT_HOUR,
    ),
    "lifetime_production": ("Envoy Lifetime Energy Production", ENERGY_WATT_HOUR),
    "consumption": ("Envoy Current Energy Consumption", POWER_WATT),
    "daily_consumption": ("Envoy Today's Energy Consumption", ENERGY_WATT_HOUR),
    "seven_days_consumption": (
        "Envoy Last Seven Days Energy Consumption",
        ENERGY_WATT_HOUR,
    ),
    "lifetime_consumption": ("Envoy Lifetime Energy Consumption", ENERGY_WATT_HOUR),
    "inverters": ("Envoy Inverter", POWER_WATT),
}

ICON = "mdi:flash"
CONST_DEFAULT_HOST = "envoy"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_IP_ADDRESS, default=CONST_DEFAULT_HOST): cv.string,
        vol.Optional(CONF_USERNAME, default="envoy"): cv.string,
        vol.Optional(CONF_PASSWORD, default=""): cv.string,
        vol.Optional(CONF_MONITORED_CONDITIONS, default=list(SENSORS)): vol.All(
            cv.ensure_list, [vol.In(list(SENSORS))]
        ),
        vol.Optional(CONF_NAME, default=""): cv.string,
    }
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Enphase Envoy sensor."""
    ip_address = config[CONF_IP_ADDRESS]
    monitored_conditions = config[CONF_MONITORED_CONDITIONS]
    name = config[CONF_NAME]
    username = config[CONF_USERNAME]
    password = config[CONF_PASSWORD]

    envoy_reader = EnvoyReader(ip_address, username, password)

    async def async_update_data():
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(10):
                data = await envoy_reader.update()
                _LOGGER.debug(data)
                return data
        except ApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}")

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        # Name of the data. For logging purposes.
        name="sensor",
        update_method=async_update_data,
        # Polling interval. Will only be polled if there are subscribers.
        update_interval=timedelta(seconds=30),
    )

    # Fetch initial data so we have data when entities subscribe
    await coordinator.async_refresh()

    entities = []
    # Iterate through the list of sensors
    for condition in monitored_conditions:
        if condition == "inverters":
            try:
                inverters = await envoy_reader.inverters_production()
            except requests.exceptions.HTTPError:
                _LOGGER.warning(
                    "Authentication for Inverter data failed during setup: %s",
                    ip_address,
                )
                continue

            if isinstance(inverters, dict):
                for inverter in inverters:
                    entities.append(
                        Envoy(
                            envoy_reader,
                            condition,
                            f"{name}{SENSORS[condition][0]} {inverter}",
                            SENSORS[condition][1],
                            coordinator,
                        )
                    )
                    _LOGGER.debug("Adding inverter SN: %s - Type: %s.", f"{name}{SENSORS[condition][0]} {inverter}", condition)

        else:
            entities.append(
                Envoy(
                    envoy_reader,
                    condition,
                    f"{name}{SENSORS[condition][0]}",
                    SENSORS[condition][1],
                    coordinator,
                )
            )
            _LOGGER.debug("Adding sensor: %s - Type: %s.", f"{name}{SENSORS[condition][0]})", condition)
    async_add_entities(entities)    


class Envoy(Entity):

    def __init__(self, envoy_reader, sensor_type, name, unit, coordinator):
        self._envoy_reader = envoy_reader
        self._type = sensor_type
        self._name = name
        self._unit_of_measurement = unit
        self._state = None
        self._last_reported = None

        self.coordinator = coordinator

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return self._unit_of_measurement

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        return ICON

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        if self._type == "inverters":
            return {"last_reported": self._last_reported}

        return None

    @property
    def should_poll(self):
        """No need to poll. Coordinator notifies entity of updates."""
        return True

    @property
    def available(self):
        """Return if entity is available."""
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self.async_on_remove(
            self.coordinator.async_add_listener(
                self.async_write_ha_state
            )
        )

    async def async_update(self):
        """Update the energy production data."""
        if self._type != "inverters":
            if isinstance(self.coordinator.data.get(self._type), int):
                self._state = self.coordinator.data.get(self._type)
                _LOGGER.debug(
                    "Updating: %s - %s", self._type, self._state
                )
            else:
                _LOGGER.debug(
                    "Sensor %s isInstance(int) was %s.  Returning None for state.",
                    self._type,
                    isinstance(self.coordinator.data.get(self._type), int),
                )

        elif self._type == "inverters":
            serial_number = self._name.split(" ")[2]
            if isinstance(self.coordinator.data.get("inverters_production"), dict):
                self._state = self.coordinator.data.get("inverters_production").get(
                    serial_number
                )[0]
                _LOGGER.debug(
                    "Updating: %s (%s) - %s.",
                    self._type,
                    serial_number,
                    self._state,
                )
            else:
                _LOGGER.debug(
                    "Data inverter (%s) isInstance(dict) was %s.  Using previous state: %s",
                    serial_number,
                    isinstance(self.coordinator.data.get("inverters_production"), dict),
                    self._state,
                )

        