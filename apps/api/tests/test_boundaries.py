import io
import shutil
import zipfile
from datetime import datetime, timezone

import httpx
import pytest
import shapefile
from pyproj import Transformer
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.entities import DatasetVersion, DistrictBoundary, GeographicArea, SourceCheck
from app.services.connectors.base import ConnectorError, SourceAsset
from app.services.connectors.catalogs import BANGKOK_EXTENT, BangkokBoundaryConnector
from app.services import pipeline
from app.services.pipeline import run_monitor


PRJ_32647 = 'PROJCS["WGS 84 / UTM zone 47N",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",99],PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],PARAMETER["false_northing",0],UNIT["metre",1],AUTHORITY["EPSG","32647"]]'


def _boundary_zip() -> bytes:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    writer = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON, encoding="cp874")
    writer.field("OBJECTID", "N", size=8)
    writer.field("AREA", "N", size=20, decimal=4)
    writer.field("dcode", "C", size=10)
    writer.field("dname", "C", size=80)
    writer.field("dname_e", "C", size=80)
    transformer = Transformer.from_crs(4326, 32647, always_xy=True)
    named = {
        0: ("เขตปทุมวัน", "Pathum Wan"),
        1: ("เขตคลองเตย", "Khlong Toei"),
        2: ("เขตบางเขน", "Bang Khen"),
    }
    for index in range(50):
        column, row = index % 10, index // 10
        west, south = 100.35 + column * 0.055, 13.52 + row * 0.065
        east, north = west + 0.025, south + 0.03
        ring_lonlat = [(west, south), (west, north), (east, north), (east, south), (west, south)]
        ring_utm = [transformer.transform(lon, lat) for lon, lat in ring_lonlat]
        area = abs((ring_utm[2][0] - ring_utm[0][0]) * (ring_utm[2][1] - ring_utm[0][1]))
        name_th, name_en = named.get(index, ("เขตทดสอบ%02d" % (index + 1), "Test District %02d" % (index + 1)))
        writer.poly([ring_utm])
        writer.record(index + 1, area, "10%02d" % (index + 1), name_th, name_en)
    writer.close()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("district.shp", shp.getvalue())
        archive.writestr("district.shx", shx.getvalue())
        archive.writestr("district.dbf", dbf.getvalue())
        archive.writestr("district.prj", PRJ_32647)
    return output.getvalue()


def _demo_areas(db):
    for area_id, name_th, name_en in (
        ("demo-pathumwan", "ปทุมวัน (สาธิต)", "Pathum Wan (demo)"),
        ("demo-khlong-toei", "คลองเตย (สาธิต)", "Khlong Toei (demo)"),
        ("demo-bang-khen", "บางเขน (สาธิต)", "Bang Khen (demo)"),
    ):
        db.add(
            GeographicArea(
                id=area_id,
                name_th=name_th,
                name_en=name_en,
                geometry={"type": "MultiPolygon", "coordinates": [[[[100.5, 13.7], [100.51, 13.7], [100.51, 13.71], [100.5, 13.71], [100.5, 13.7]]]]},
                quality_status="synthetic_demo",
            )
        )
    db.commit()


@pytest.mark.parametrize("resource_format", ["ZIP", "shapfile"])
def test_official_boundary_ingestion_replaces_demo_geometry_and_versions_history(db, monkeypatch, resource_format):
    _demo_areas(db)
    archive = _boundary_zip()
    state = {"modified": "2021-03-09T12:57:47+00:00", "downloads": 0}

    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        if "package_show" in url:
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {
                        "id": "e537025b-1cf6-4c5b-8e46-c2e976f13283",
                        "title": "พื้นที่เขตปกครอง 50 เขตของกรุงเทพมหานคร",
                        "metadata_modified": "2025-04-27T08:51:52+00:00",
                        "license_title": "License not specified",
                        "resources": [
                            {
                                "id": "d7be7e84-9d84-4595-bf8f-79b0bc01f1ae",
                                "name": "50 เขตของกรุงเทพมหานคร",
                                "format": resource_format,
                                "last_modified": state["modified"],
                                "url": "https://data.bangkok.go.th/download/district.zip",
                            }
                        ],
                    },
                },
                request=request,
            )
        state["downloads"] += 1
        return httpx.Response(200, content=archive, request=request)

    monkeypatch.setattr(httpx, "get", fake_get)
    first = run_monitor(db, BangkokBoundaryConnector(), requested_by="test")

    areas = db.scalars(select(GeographicArea).order_by(GeographicArea.id)).all()
    version = db.scalar(select(DatasetVersion).where(DatasetVersion.source_asset_id.like("bma-boundaries:%")))
    assert first["processed"] == 1
    assert len(areas) == 50
    assert int(db.scalar(select(func.count()).select_from(DistrictBoundary)) or 0) == 50
    assert version.status == "published"
    assert len(version.checksum) == 64
    assert db.get(GeographicArea, "demo-pathumwan").name_th == "เขตปทุมวัน"
    assert db.get(GeographicArea, "demo-pathumwan").quality_status == "verified_official_boundary"
    assert state["downloads"] == 1

    no_change = run_monitor(db, BangkokBoundaryConnector(), requested_by="test")
    assert no_change["processed"] == 0
    assert state["downloads"] == 1

    state["modified"] = "2026-11-04T00:00:00+00:00"
    updated = run_monitor(db, BangkokBoundaryConnector(), requested_by="test")
    active_boundaries = db.scalars(select(DistrictBoundary).where(DistrictBoundary.valid_to.is_(None))).all()
    historic_boundaries = db.scalars(select(DistrictBoundary).where(DistrictBoundary.valid_to.is_not(None))).all()
    assert updated["processed"] == 1
    assert len(active_boundaries) == 50
    assert len(historic_boundaries) == 50
    assert state["downloads"] == 2


@pytest.mark.parametrize("metadata", [
    {"resource_format": "CSV", "resource_url": "https://data.bangkok.go.th/download/district.zip"},
    {"resource_format": "shapfile", "resource_url": "https://data.bangkok.go.th/download/district.csv"},
    {"resource_format": "shapfile", "resource_url": "https://unapproved.example/district.zip"},
])
def test_boundary_format_alias_still_requires_approved_zip_resource(metadata):
    asset = SourceAsset.with_checksum("isolated-test-resource", "test-version", datetime.now(timezone.utc),
                                      BANGKOK_EXTENT, metadata=metadata)
    assert BangkokBoundaryConnector().validate(asset)["passed"] is False


def test_bundled_official_boundaries_publish_only_as_bootstrap_when_origin_unreachable(db, monkeypatch):
    def unreachable(*args, **kwargs):
        raise httpx.ConnectTimeout("origin unreachable")

    monkeypatch.setattr(httpx, "get", unreachable)
    connector = BangkokBoundaryConnector()
    result = run_monitor(db, connector, requested_by="test")
    version = db.scalar(select(DatasetVersion))
    assert result["status"] == "degraded" and result["processed"] == 1
    assert version.status == "published"
    assert version.observed_at.date().isoformat() == "2020-08-11"
    assert version.metadata_json["license"] == "Creative Commons Attributions"
    assert version.checksum == version.metadata_json["snapshot_archive_sha256"]
    assert db.scalar(select(func.count()).select_from(GeographicArea)) == 50
    assert db.scalar(select(SourceCheck)).status == "degraded"

    with pytest.raises(ConnectorError, match="origin unreachable"):
        run_monitor(db, connector, requested_by="test")
    assert db.scalar(select(func.count()).select_from(DatasetVersion)) == 1
    assert db.get(DatasetVersion, version.id).status == "published"


def test_corrupted_boundary_snapshot_is_never_published(db, monkeypatch, tmp_path):
    original = settings.bma_boundary_snapshot_dir
    shutil.copyfile(original / "manifest.json", tmp_path / "manifest.json")
    archive = next(original.glob("*.zip"))
    (tmp_path / archive.name).write_bytes(b"invalid archive")
    monkeypatch.setattr(settings, "bma_boundary_snapshot_dir", tmp_path)

    def unreachable(*args, **kwargs):
        raise httpx.ConnectError("origin unreachable")

    monkeypatch.setattr(httpx, "get", unreachable)
    with pytest.raises(ConnectorError, match="does not match its checksum"):
        run_monitor(db, BangkokBoundaryConnector(), requested_by="test")
    assert db.scalar(select(func.count()).select_from(GeographicArea)) == 0
    assert db.scalar(select(DatasetVersion)).status == "failed"


def test_publication_error_rolls_back_boundaries_and_allows_retry(db, monkeypatch):
    def unreachable(*args, **kwargs):
        raise httpx.ConnectTimeout("origin unreachable")

    monkeypatch.setattr(httpx, "get", unreachable)
    with monkeypatch.context() as failure:
        def fail_after_boundary_writes(*args):
            raise IntegrityError("INSERT", {"value": "private"}, RuntimeError("injected failure"))

        failure.setattr(pipeline, "_persist_raster_metadata", fail_after_boundary_writes)
        with pytest.raises(ConnectorError, match="publication failed \\(IntegrityError\\)"):
            run_monitor(db, BangkokBoundaryConnector(), requested_by="test")
    version = db.scalar(select(DatasetVersion))
    assert version.status == "failed" and version.quality_status == "processing_error"
    assert "private" not in db.scalar(select(SourceCheck)).error_message
    assert db.scalar(select(func.count()).select_from(GeographicArea)) == 0
    assert run_monitor(db, BangkokBoundaryConnector(), requested_by="test")["processed"] == 1
    assert db.get(DatasetVersion, version.id).status == "published"
