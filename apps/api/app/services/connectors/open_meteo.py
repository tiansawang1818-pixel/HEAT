from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.services.connectors.base import ConnectorError, SourceAsset, SourceConnector


class OpenMeteoConnector(SourceConnector):
    source_name = "Open-Meteo"
    dataset_id = "open-meteo/weather-context"
    layer_key = "weather"

    def discover(self, latest_asset_id: Optional[str] = None, checkpoint: Optional[Dict[str, Any]] = None) -> List[SourceAsset]:
        params = {
            "latitude": settings.open_meteo_latitude,
            "longitude": settings.open_meteo_longitude,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "hourly": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation_probability",
            "forecast_days": 2,
            "timezone": "UTC",
        }
        try:
            response = httpx.get(settings.open_meteo_base_url, params=params, timeout=settings.source_request_timeout_seconds)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ConnectorError("Open-Meteo request failed: %s" % exc) from exc
        data = response.json()
        observed_raw = data.get("current", {}).get("time")
        if not observed_raw:
            raise ConnectorError("Open-Meteo response has no current reference time")
        observed_at = datetime.fromisoformat(observed_raw.replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        model = data.get("generationtime_ms", "unknown")
        asset_id = "open-meteo:%s" % observed_at.isoformat()
        if latest_asset_id == asset_id:
            return []
        point = {"type": "Point", "coordinates": [float(data["longitude"]), float(data["latitude"])]}
        return [
            SourceAsset.with_checksum(
                asset_id,
                "forecast-api-v1",
                observed_at,
                point,
                metadata={
                    "provider_model": data.get("timezone_abbreviation", "UTC"),
                    "reference_time": observed_at.isoformat(),
                    "generation_time_ms": model,
                    "data_class": "model_estimate_and_forecast",
                },
                payload={"current": data.get("current", {}), "current_units": data.get("current_units", {})},
            )
        ]
