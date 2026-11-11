"use client";

import { useEffect, useRef } from "react";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { GeoJsonLayer, ScatterplotLayer } from "@deck.gl/layers";
import type { PickingInfo } from "@deck.gl/core";
import * as maplibregl from "maplibre-gl";
import type { Map as MapLibreMap } from "maplibre-gl";
import { populationColorScale } from "@/lib/utils";
import type {
  AreaProperties,
  FeatureCollection,
  GeoFeature,
  OptimizationProject,
  RasterTileDescriptor,
} from "@/lib/utils";

const BANGKOK: [number, number] = [100.5018, 13.7563];

function ramp(value: number | null | undefined, layer: string): [number, number, number, number] {
  if (value == null || !Number.isFinite(value)) return [72, 93, 106, 115];
  if (layer === "vegetation" || layer === "ndvi") {
    const t = Math.min(1, Math.max(0, layer === "ndvi" ? value : value / 100));
    return [29, 110 + 105 * t, 117 - 45 * t, 195];
  }
  if (layer === "population") {
    const { min, max, from, to } = populationColorScale;
    const t = Math.min(1, Math.max(0, (value - min) / (max - min)));
    return [from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t, from[2] + (to[2] - from[2]) * t, 205];
  }
  if (layer === "built" || layer === "built_up") {
    const t = Math.min(1, value / 100);
    return [96 + 110 * t, 90 - 25 * t, 130 - 35 * t, 200];
  }
  const normalized = layer === "temperature" ? (value - 25) / 20
    : layer === "future-risk" ? (value + 0.5) / 4
    : layer === "historical" ? (value + 1) / 4  // °C per year, about -1 to +3
    : value / 100;
  const t = Math.min(1, Math.max(0, normalized));
  return [255, 185 - 145 * t, 45 - 25 * t, 205];
}

function layerValue(feature: { properties: AreaProperties }, layer: string) {
  const p = feature.properties;
  const valueByLayer: Record<string, number | null | undefined> = {
    temperature: p.surface_temperature_c,
    priority: p.priority_score,
    vegetation: p.tree_coverage_pct,
    ndvi: p.ndvi,
    population: p.population_density,
    built: p.built_up_pct,
    built_up: p.built_up_pct,
    "future-risk": p.scenario_change_c,
    historical: p.temperature_trend_c_per_year,
  };
  return valueByLayer[layer];
}

interface HeatMapProps {
  data: FeatureCollection;
  activeLayer: string;
  loading?: boolean;
  rasterTile?: RasterTileDescriptor | null;
  selectedId?: string;
  projects?: OptimizationProject[];
  onSelect: (feature: GeoFeature) => void;
  onMapReady?: () => void;
  viewport?: MapViewport;
  onViewportChange?: (viewport: MapViewport) => void;
  compact?: boolean;
  mapLabel?: string;
}

export interface MapViewport {
  center: [number, number];
  zoom: number;
  bearing: number;
  pitch: number;
  bounds?: [number, number, number, number];
}

export function HeatMap({
  data,
  activeLayer,
  loading = false,
  rasterTile = null,
  selectedId,
  projects = [],
  onSelect,
  onMapReady,
  viewport,
  onViewportChange,
  compact = false,
  mapLabel = "แผนที่เชิงโต้ตอบกรุงเทพมหานคร",
}: HeatMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const readyCallbackRef = useRef(onMapReady);
  const viewportCallbackRef = useRef(onViewportChange);
  const initialViewportRef = useRef(viewport);
  const hasOfficialBoundaries = data.features.some((feature) => feature.properties.boundary_quality_status === "verified_official_boundary");

  useEffect(() => {
    readyCallbackRef.current = onMapReady;
    viewportCallbackRef.current = onViewportChange;
  }, [onMapReady, onViewportChange]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");
    const initialViewport = initialViewportRef.current;
    const map = new maplibregl.Map({
      container: containerRef.current,
      center: initialViewport?.center || BANGKOK,
      zoom: initialViewport?.zoom || 10.4,
      bearing: initialViewport?.bearing || 0,
      pitch: initialViewport?.pitch || 0,
      minZoom: 8.5,
      maxZoom: 18,
      attributionControl: false,
      style: process.env.NEXT_PUBLIC_MAP_STYLE_URL || "/styles/community-osm.json",
    });
    if (!compact) map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-left");
    map.addControl(new maplibregl.AttributionControl({ compact: false }), "bottom-right");
    const overlay = new MapboxOverlay({ interleaved: false, layers: [] });
    map.addControl(overlay as unknown as maplibregl.IControl);
    const emitViewport = () => {
      const center = map.getCenter();
      const bounds = map.getBounds();
      viewportCallbackRef.current?.({
        center: [center.lng, center.lat],
        zoom: map.getZoom(),
        bearing: map.getBearing(),
        pitch: map.getPitch(),
        bounds: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()],
      });
    };
    map.on("load", () => {
      readyCallbackRef.current?.();
      emitViewport();
    });
    map.on("moveend", emitViewport);
    mapRef.current = map;
    overlayRef.current = overlay;
    return () => {
      overlay.finalize();
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
    };
  }, [compact]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !viewport) return;
    const center = map.getCenter();
    const isSame = Math.abs(center.lng - viewport.center[0]) < 0.00001
      && Math.abs(center.lat - viewport.center[1]) < 0.00001
      && Math.abs(map.getZoom() - viewport.zoom) < 0.001
      && Math.abs(map.getBearing() - viewport.bearing) < 0.01
      && Math.abs(map.getPitch() - viewport.pitch) < 0.01;
    if (!isSame) {
      map.jumpTo({ center: viewport.center, zoom: viewport.zoom, bearing: viewport.bearing, pitch: viewport.pitch });
    }
  }, [viewport]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const sourceId = "heatshift-raster-source";
    const layerId = "heatshift-raster-layer";
    const applyRaster = () => {
      if (map.getLayer(layerId)) map.removeLayer(layerId);
      if (map.getSource(sourceId)) map.removeSource(sourceId);
      if (!rasterTile) return;
      map.addSource(sourceId, {
        type: "raster",
        tiles: rasterTile.tiles,
        tileSize: rasterTile.tile_size,
        minzoom: rasterTile.min_zoom,
        maxzoom: rasterTile.max_zoom,
        bounds: rasterTile.bounds,
        attribution: rasterTile.attribution,
      });
      map.addLayer({
        id: layerId,
        type: "raster",
        source: sourceId,
        paint: {
          "raster-opacity": 0.72,
          "raster-fade-duration": 180,
        },
      });
    };
    if (map.isStyleLoaded()) applyRaster();
    else map.once("load", applyRaster);
    return () => {
      map.off("load", applyRaster);
    };
  }, [rasterTile]);

  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    const areaLayer = new GeoJsonLayer<AreaProperties & Record<string, unknown>>({
      id: `bangkok-${activeLayer}`,
      data: data.features,
      pickable: true,
      stroked: true,
      filled: true,
      getFillColor: (feature) => rasterTile || loading ? [0, 0, 0, 1] : ramp(layerValue(feature, activeLayer), activeLayer),
      getLineColor: (feature) => feature.properties.id === selectedId ? [78, 234, 255, 255] : [192, 226, 235, 145],
      getLineWidth: (feature) => feature.properties.id === selectedId ? 4 : 1,
      lineWidthMinPixels: 1,
      updateTriggers: { getFillColor: [activeLayer, rasterTile?.dataset_version_id, loading], getLineColor: [selectedId], getLineWidth: [selectedId] },
      onClick: (info: PickingInfo) => info.object && onSelect(info.object as GeoFeature),
      autoHighlight: true,
      highlightColor: [78, 234, 255, 55],
    });
    const points = projects
      .filter((project) => project.latitude != null && project.longitude != null)
      .map((project) => ({ ...project, position: [project.longitude!, project.latitude!, 0] as [number, number, number] }));
    const projectLayer = new ScatterplotLayer({
      id: "optimized-projects",
      data: points,
      pickable: true,
      getPosition: (project: (typeof points)[number]) => project.position,
      getRadius: 180,
      radiusMinPixels: 7,
      getFillColor: [78, 234, 255, 230],
      getLineColor: [255, 255, 255, 255],
      stroked: true,
      lineWidthMinPixels: 2,
    });
    overlay.setProps({
      layers: [areaLayer, projectLayer],
      getTooltip: ({ object }: { object?: GeoFeature | OptimizationProject }) => {
        if (!object) return null;
        if ("properties" in object) {
          const value = layerValue(object, activeLayer);
          return {
            text: `${object.properties.name_th}\n${loading ? "กำลังโหลดข้อมูลชั้นนี้…" : value == null ? "ยังไม่มีค่าที่ผ่านการตรวจสอบ" : `${value.toLocaleString("th-TH", { maximumFractionDigits: 2 })}${activeLayer === "population" ? " คน/ตร.กม." : activeLayer === "historical" ? " °C/ปี" : ""}`}`,
            style: { backgroundColor: "#0d1922", color: "#f8fafc", border: "1px solid rgba(255,255,255,.16)", fontSize: "12px", whiteSpace: "pre-line" },
          };
        }
        return { text: object.name };
      },
    });
  }, [activeLayer, data, loading, onSelect, projects, rasterTile, selectedId]);

  return (
    <div className="absolute inset-0">
      <div ref={containerRef} className="h-full w-full" aria-label={mapLabel} />
      {hasOfficialBoundaries ? (
        <a
          className="absolute bottom-7 right-1 z-10 rounded bg-ink/90 px-2 py-1 text-[10px] text-slate-200 underline"
          href="https://data.go.th/th/dataset/district"
          target="_blank"
          rel="noreferrer"
        >
          ขอบเขตเขต · กรุงเทพมหานคร
        </a>
      ) : !compact && (
        <div className="pointer-events-none absolute bottom-7 left-1/2 z-10 -translate-x-1/2 rounded-full border border-white/10 bg-ink/85 px-3 py-1.5 font-mono text-[9px] uppercase tracking-[.16em] text-slate-400 backdrop-blur">
          MapLibre GL · deck.gl · WGS84
        </div>
      )}
    </div>
  );
}
