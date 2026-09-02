"""Constants for the Kniha jízd integration."""

from typing import Final

DOMAIN: Final = "kniha_jizd"
NAME: Final = "Kniha jízd"

CONF_TRIGGER_ENTITY: Final = "trigger_entity"
CONF_GPS_ENTITY: Final = "gps_entity"
CONF_ADDRESS_ENTITY: Final = "address_entity"
CONF_ODOMETER_ENTITY: Final = "odometer_entity"
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_LOCATION_SETTLE_SECONDS: Final = "location_settle_seconds"
CONF_RETURN_CONTEXT_HOURS: Final = "return_context_hours"
CONF_TRANSIENT_STOP_MINUTES: Final = "transient_stop_minutes"
CONF_HOME_ADDRESS: Final = "home_address"
CONF_HOME_LATITUDE: Final = "home_latitude"
CONF_HOME_LONGITUDE: Final = "home_longitude"
CONF_COMPANY_ADDRESS: Final = "company_address"
CONF_COMPANY_LATITUDE: Final = "company_latitude"
CONF_COMPANY_LONGITUDE: Final = "company_longitude"
CONF_COMPANY_LABEL: Final = "company_label"
# Legacy combined radius retained only for config-entry migration.
CONF_PLACE_RADIUS: Final = "place_radius"
CONF_HOME_RADIUS: Final = "home_radius"
CONF_COMPANY_RADIUS: Final = "company_radius"
CONF_CLIENT_RADIUS: Final = "client_radius"
CONF_PRIVATE_RADIUS: Final = "private_radius"
CONF_TRANSIENT_RADIUS: Final = "transient_radius"
CONF_INSTITUTION_SEARCH_RADIUS: Final = "institution_search_radius"
CONF_PENDING_REVIEW_HOURS: Final = "pending_review_hours"
CONF_OVERPASS_URL: Final = "overpass_url"
CONF_RELEVANCE_KEYWORDS: Final = "relevance_keywords"
CONF_NOMINATIM_URL: Final = "nominatim_url"
CONF_NOMINATIM_USER_AGENT: Final = "nominatim_user_agent"
CONF_NOMINATIM_EMAIL: Final = "nominatim_email"

DEFAULT_TRIGGER_ENTITY: Final = "binary_sensor.android_auto"
DEFAULT_GPS_ENTITY: Final = "device_tracker.telefon"
DEFAULT_ADDRESS_ENTITY: Final = "sensor.telefon_geocoded_location"
DEFAULT_ODOMETER_ENTITY: Final = "sensor.skoda_odometer"
DEFAULT_NOTIFY_SERVICE: Final = "mobile_app_telefon"
DEFAULT_LOCATION_SETTLE_SECONDS: Final = 60
DEFAULT_RETURN_CONTEXT_HOURS: Final = 18
DEFAULT_TRANSIENT_STOP_MINUTES: Final = 60
DEFAULT_HOME_ADDRESS: Final = ""
DEFAULT_HOME_LATITUDE: Final = ""
DEFAULT_HOME_LONGITUDE: Final = ""
DEFAULT_COMPANY_ADDRESS: Final = ""
DEFAULT_COMPANY_LATITUDE: Final = ""
DEFAULT_COMPANY_LONGITUDE: Final = ""
DEFAULT_COMPANY_LABEL: Final = "Firma"
LEARNED_TRANSIENT_RADIUS: Final = 200
LEARNED_PRIVATE_RADIUS: Final = 250
# DEFAULT_PLACE_RADIUS is the pre-1.11 legacy value.
DEFAULT_PLACE_RADIUS: Final = 1000
DEFAULT_HOME_RADIUS: Final = 300
DEFAULT_COMPANY_RADIUS: Final = 300
DEFAULT_CLIENT_RADIUS: Final = 500
DEFAULT_PRIVATE_RADIUS: Final = 250
DEFAULT_TRANSIENT_RADIUS: Final = 200
DEFAULT_INSTITUTION_SEARCH_RADIUS: Final = 3000
DEFAULT_PENDING_REVIEW_HOURS: Final = 24
DEFAULT_OVERPASS_URL: Final = "https://overpass-api.de/api/interpreter"
DEFAULT_RELEVANCE_KEYWORDS: Final = (
    "genet, genom, dna, molekul, biomed, laborato, cytogen, sekven, "
    "patolog, onkolog, mikrobiolog, krev, hematol, transfuz, blood"
)
DEFAULT_NOMINATIM_URL: Final = "https://nominatim.openstreetmap.org/reverse"
DEFAULT_NOMINATIM_USER_AGENT: Final = "HomeAssistant-kniha-jizd/1.0"

RAW_DATA_FILENAME: Final = "kniha_jizd_raw.json"
LEARNED_PLACES_FILENAME: Final = "learned_places.json"
RUNTIME_STORE_VERSION: Final = 1

SERVICE_EXPORT_EXCEL: Final = "export_excel"
SERVICE_UPDATE_TRIP: Final = "update_trip"
SERVICE_RESOLVE_TRIP: Final = "resolve_trip"
SERVICE_SAVE_TRIP_PLACE: Final = "save_trip_place"
ATTR_PATH: Final = "path"
ATTR_MONTH: Final = "month"
ATTR_SEGMENT_ID: Final = "segment_id"
ATTR_PURPOSE: Final = "purpose"
ATTR_TRIP_TYPE: Final = "trip_type"
ATTR_START_ADDRESS: Final = "start_address"
ATTR_END_ADDRESS: Final = "end_address"
ATTR_DISTANCE_KM: Final = "distance_km"
ATTR_ACTION: Final = "action"
ATTR_VALUE: Final = "value"
ATTR_CANDIDATE_INDEX: Final = "candidate_index"
DEFAULT_EXPORT_PATH: Final = "kniha_jizd.xlsx"

EVENT_NOTIFICATION_ACTION: Final = "mobile_app_notification_action"
ACTION_PREFIX: Final = "KNIHA_JIZD"
ACTION_CONFIRM: Final = "CONFIRM"
ACTION_NEW: Final = "NEW"
ACTION_BUSINESS: Final = "BUSINESS"
ACTION_PRIVATE: Final = "PRIVATE"
ACTION_RETURN: Final = "RETURN"
ACTION_SAVE_PLACE: Final = "SAVE_PLACE"
ACTION_SAVE_NAMED_PLACE: Final = "SAVE_NAMED_PLACE"
ACTION_SKIP_PLACE: Final = "SKIP_PLACE"

TRIP_TYPE_BUSINESS: Final = "business"
TRIP_TYPE_PRIVATE: Final = "private"
TRIP_TYPE_CONTEXTUAL: Final = "contextual"
TRIP_TYPE_UNCLASSIFIED: Final = "unclassified"

PLACE_ROLE_CLIENT: Final = "client"
PLACE_ROLE_MIXED: Final = "mixed"
PLACE_ROLE_PRIVATE: Final = "private"
PLACE_ROLE_RETURN: Final = "return"
PLACE_ROLE_TRANSIENT: Final = "transient"

UNAVAILABLE_STATES: Final = frozenset({"unknown", "unavailable", "none", ""})
