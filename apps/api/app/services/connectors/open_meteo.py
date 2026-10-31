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
                    "provider_model": "best_match",
                    "provider_timezone": data.get("timezone", "UTC"),
                    "reference_time": observed_at.isoformat(),
                    "generation_time_ms": model,
                    "data_class": "model_estimate_and_forecast",
                },
                payload={
                    "current": data.get("current", {}),
                    "current_units": data.get("current_units", {}),
                    "hourly": data.get("hourly", {}),
                    "hourly_units": data.get("hourly_units", {}),
                },
            )
        ]

    def validate(self, asset: SourceAsset) -> Dict[str, Any]:
        report = super().validate(asset)
        errors = list(report["errors"])
        current = asset.payload.get("current", {}) if isinstance(asset.payload, dict) else {}
        units = asset.payload.get("current_units", {}) if isinstance(asset.payload, dict) else {}
        hourly = asset.payload.get("hourly", {}) if isinstance(asset.payload, dict) else {}
        hourly_units = asset.payload.get("hourly_units", {}) if isinstance(asset.payload, dict) else {}
        required = ["time", "temperature_2m", "relative_humidity_2m", "apparent_temperature", "wind_speed_10m"]
        missing = [field for field in required if current.get(field) is None]
        if missing:
            errors.append("missing Open-Meteo current fields: %s" % ", ".join(missing))
        for field in ["temperature_2m", "relative_humidity_2m", "apparent_temperature", "wind_speed_10m"]:
            if field in current and not isinstance(current[field], (int, float)):
                errors.append("Open-Meteo field %s must be numeric" % field)
        if not units.get("temperature_2m") or not units.get("wind_speed_10m"):
            errors.append("Open-Meteo response is missing required units")
        hourly_required = ["time", "temperature_2m", "apparent_temperature", "relative_humidity_2m", "precipitation_probability"]
        missing_hourly = [field for field in hourly_required if not isinstance(hourly.get(field), list) or not hourly[field]]
        if missing_hourly:
            errors.append("missing Open-Meteo hourly fields: %s" % ", ".join(missing_hourly))
        elif len({len(hourly[field]) for field in hourly_required}) != 1:
            errors.append("Open-Meteo hourly fields have inconsistent lengths")
        if not hourly_units.get("temperature_2m") or not hourly_units.get("precipitation_probability"):
            errors.append("Open-Meteo hourly response is missing required units")
        return {
            "passed": not errors,
            "errors": errors,
            "warnings": report["warnings"],
            "metrics": {
                "current_fields_present": len(required) - len(missing),
                "hourly_fields_present": len(hourly_required) - len(missing_hourly),
                "forecast_hours": len(hourly.get("time", [])) if isinstance(hourly.get("time"), list) else 0,
                "data_class": asset.metadata.get("data_class"),
            },
        }
