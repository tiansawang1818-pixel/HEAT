"use client";

import { useEffect, useRef } from "react";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { GeoJsonLayer, ScatterplotLayer } from "@deck.gl/layers";
import type { PickingInfo } from "@deck.gl/core";
import * as maplibregl from "maplibre-gl";
import type { Map as MapLibreMap } from "maplibre-gl";
import type { AreaProperties, FeatureCollection, GeoFeature, OptimizationProject } from "@/lib/utils";

const BANGKOK: [number, number] = [100.5018, 13.7563];

function ramp(value: number | null | undefined, layer: string): [number, number, number, number] {
  if (value == null || !Number.isFinite(value)) return [72, 93, 106, 115];
  if (layer === "vegetation" || layer === "ndvi") {
    const t = Math.min(1, Math.max(0, layer === "ndvi" ? value : value / 100));
    return [29, 110 + 105 * t, 117 - 45 * t, 195];
  }
  if (layer === "population") {
    const t = Math.min(1, value / 20_000);
    return [58 + 100 * t, 115 - 45 * t, 180 + 65 * t, 205];
  }
  if (layer === "built" || layer === "built_up") {
    const t = Math.min(1, value / 100);
    return [96 + 110 * t, 90 - 25 * t, 130 - 35 * t, 200];
  }
  const normalized = layer === "temperature" ? (value - 25) / 20 : value / 100;
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
    population: p.population,
    built: p.built_up_pct,
    built_up: p.built_up_pct,
  };
  return valueByLayer[layer];
}

interface HeatMapProps {
  data: FeatureCollection;
  activeLayer: string;
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
}

export function HeatMap({
  data,
  activeLayer,
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
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [
          { id: "background", type: "background", paint: { "background-color": "#071016" } },
          {
            id: "osm",
            type: "raster",
            source: "osm",
            paint: { "raster-saturation": -0.85, "raster-brightness-max": 0.52, "raster-contrast": 0.22 },
          },
        ],
      },
    });
    if (!compact) map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-left");
    map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    const overlay = new MapboxOverlay({ interleaved: false, layers: [] });
    map.addControl(overlay as unknown as maplibregl.IControl);
    map.on("load", () => readyCallbackRef.current?.());
    map.on("moveend", () => {
      const center = map.getCenter();
      viewportCallbackRef.current?.({
        center: [center.lng, center.lat],
        zoom: map.getZoom(),
        bearing: map.getBearing(),
        pitch: map.getPitch(),
      });
    });
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
    const overlay = overlayRef.current;
    if (!overlay) return;
    const areaLayer = new GeoJsonLayer<AreaProperties & Record<string, unknown>>({
      id: `bangkok-${activeLayer}`,
      data: data.features,
      pickable: true,
      stroked: true,
      filled: true,
      getFillColor: (feature) => ramp(layerValue(feature, activeLayer), activeLayer),
      getLineColor: (feature) => feature.properties.id === selectedId ? [78, 234, 255, 255] : [192, 226, 235, 145],
      getLineWidth: (feature) => feature.properties.id === selectedId ? 4 : 1,
      lineWidthMinPixels: 1,
      updateTriggers: { getFillColor: [activeLayer], getLineColor: [selectedId], getLineWidth: [selectedId] },
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
            html: `<div style="font-weight:700">${object.properties.name_th}</div><div>${value == null ? "ยังไม่มีค่าที่ผ่านการตรวจสอบ" : value.toLocaleString("th-TH")}</div>`,
            style: { backgroundColor: "#0d1922", color: "#f8fafc", border: "1px solid rgba(255,255,255,.16)", fontSize: "12px" },
          };
        }
        return { text: object.name };
      },
    });
  }, [activeLayer, data, onSelect, projects, selectedId]);

  return (
    <div className="absolute inset-0">
      <div ref={containerRef} className="h-full w-full" aria-label={mapLabel} />
      {!compact && (
        <div className="pointer-events-none absolute bottom-7 left-1/2 z-10 -translate-x-1/2 rounded-full border border-white/10 bg-ink/85 px-3 py-1.5 font-mono text-[9px] uppercase tracking-[.16em] text-slate-400 backdrop-blur">
          MapLibre GL · deck.gl · WGS84
        </div>
      )}
    </div>
  );
}
