from app.services.connectors import (
    BangkokBoundaryConnector, DynamicWorldConnector, LandsatConnector,
    OpenMeteoConnector, OpenStreetMapConnector, SentinelConnector, WorldPopConnector,
)


CONNECTOR_TYPES = {
    "bma-boundaries": BangkokBoundaryConnector,
    "landsat": LandsatConnector,
    "sentinel": SentinelConnector,
    "dynamic-world": DynamicWorldConnector,
    "open-meteo": OpenMeteoConnector,
    "worldpop": WorldPopConnector,
    "osm": OpenStreetMapConnector,
}
SOURCE_KEYS = {connector.dataset_id: key for key, connector in CONNECTOR_TYPES.items()}
