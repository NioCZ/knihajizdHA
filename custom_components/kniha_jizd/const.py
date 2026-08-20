"""Constants for the Kniha jízd integration."""

from typing import Final

DOMAIN: Final = "kniha_jizd"
NAME: Final = "Kniha jízd"

CONF_TRIGGER_ENTITY: Final = "trigger_entity"
CONF_GPS_ENTITY: Final = "gps_entity"
CONF_ADDRESS_ENTITY: Final = "address_entity"
CONF_ODOMETER_ENTITY: Final = "odometer_entity"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_WAIT_TIMEOUT: Final = "wait_timeout"
CONF_PLACE_RADIUS: Final = "place_radius"
CONF_NOMINATIM_URL: Final = "nominatim_url"
CONF_NOMINATIM_USER_AGENT: Final = "nominatim_user_agent"
CONF_NOMINATIM_EMAIL: Final = "nominatim_email"

DEFAULT_TRIGGER_ENTITY: Final = "binary_sensor.android_auto"
DEFAULT_GPS_ENTITY: Final = "device_tracker.telefon"
DEFAULT_ADDRESS_ENTITY: Final = "sensor.telefon_geocoded_location"
DEFAULT_ODOMETER_ENTITY: Final = "sensor.skoda_odometer"
DEFAULT_NOTIFY_SERVICE: Final = "mobile_app_telefon"
DEFAULT_WAIT_TIMEOUT: Final = 600
DEFAULT_PLACE_RADIUS: Final = 150
DEFAULT_NOMINATIM_URL: Final = "https://nominatim.openstreetmap.org/reverse"
DEFAULT_NOMINATIM_USER_AGENT: Final = "HomeAssistant-kniha-jizd/1.0"

RAW_DATA_FILENAME: Final = "kniha_jizd_raw.json"
LEARNED_PLACES_FILENAME: Final = "learned_places.json"
RUNTIME_STORE_VERSION: Final = 1

SERVICE_EXPORT_EXCEL: Final = "export_excel"
ATTR_PATH: Final = "path"
DEFAULT_EXPORT_PATH: Final = "www/kniha_jizd.xlsx"

EVENT_NOTIFICATION_ACTION: Final = "mobile_app_notification_action"
ACTION_PREFIX: Final = "KNIHA_JIZD"
ACTION_CONFIRM: Final = "CONFIRM"
ACTION_NEW: Final = "NEW"
ACTION_PRIVATE: Final = "PRIVATE"
ACTION_FUEL: Final = "FUEL"

TRIP_TYPE_BUSINESS: Final = "business"
TRIP_TYPE_PRIVATE: Final = "private"
TRIP_TYPE_FUELING: Final = "fueling"

UNAVAILABLE_STATES: Final = frozenset({"unknown", "unavailable", "none", ""})
