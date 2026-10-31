import type { DatasetStatus, FeatureCollection, HistoryPoint, MapLayerMeta } from "@/lib/utils";

// These fixtures intentionally contain no measured values. They provide geometry and
// UI states only when the API is unavailable, and are always accompanied by a demo banner.
export const demoFeatures: FeatureCollection = {
  type: "FeatureCollection",
  features: [
    {
      type: "Feature",
      id: "demo-pathum-wan",
      properties: {
        id: "demo-pathum-wan",
        name_th: "ปทุมวัน (พื้นที่สาธิต)",
        name_en: "Pathum Wan (demo)",
        quality_status: "demo_only",
        priority_explanation: ["รอข้อมูลที่ผ่านการตรวจสอบจาก backend"],
      },
      geometry: {
        type: "Polygon",
        coordinates: [[[100.512, 13.72], [100.558, 13.72], [100.558, 13.756], [100.512, 13.756], [100.512, 13.72]]],
      },
    },
    {
      type: "Feature",
      id: "demo-bang-kapi",
      properties: {
        id: "demo-bang-kapi",
        name_th: "บางกะปิ (พื้นที่สาธิต)",
        name_en: "Bang Kapi (demo)",
        quality_status: "demo_only",
        priority_explanation: ["รอข้อมูลที่ผ่านการตรวจสอบจาก backend"],
      },
      geometry: {
        type: "Polygon",
        coordinates: [[[100.59, 13.74], [100.66, 13.74], [100.66, 13.81], [100.59, 13.81], [100.59, 13.74]]],
      },
    },
    {
      type: "Feature",
      id: "demo-thon-buri",
      properties: {
        id: "demo-thon-buri",
        name_th: "ธนบุรี (พื้นที่สาธิต)",
        name_en: "Thon Buri (demo)",
        quality_status: "demo_only",
        priority_explanation: ["รอข้อมูลที่ผ่านการตรวจสอบจาก backend"],
      },
      geometry: {
        type: "Polygon",
        coordinates: [[[100.455, 13.69], [100.51, 13.69], [100.51, 13.75], [100.455, 13.75], [100.455, 13.69]]],
      },
    },
  ],
};

export const demoDatasets: DatasetStatus[] = [
  "Landsat 8/9",
  "Sentinel-2",
  "Dynamic World",
  "WorldPop",
  "Open-Meteo",
].map((source, index) => ({
  source_name: source,
  dataset_id: `unavailable-${index}`,
  status: "unavailable",
  quality_status: "not_loaded",
  status_reason: "backend ยังไม่พร้อมหรือยังไม่ได้ตั้งค่า credential",
}));

export const defaultLayers: MapLayerMeta[] = [
  { id: "temperature", name_th: "อุณหภูมิพื้นผิว", unit: "°C", status: "unavailable", source_name: "Landsat 8/9" },
  { id: "priority", name_th: "ลำดับความสำคัญความร้อน", unit: "คะแนน", status: "unavailable", source_name: "HEAT//SHIFT" },
  { id: "vegetation", name_th: "พื้นที่สีเขียว", unit: "%", status: "unavailable", source_name: "Dynamic World" },
  { id: "ndvi", name_th: "NDVI", status: "unavailable", source_name: "Sentinel-2" },
  { id: "population", name_th: "ความหนาแน่นประชากร", unit: "คน/กม²", status: "unavailable", source_name: "WorldPop" },
  { id: "built", name_th: "พื้นที่สิ่งปลูกสร้าง", unit: "%", status: "unavailable", source_name: "Dynamic World" },
  { id: "candidates", name_th: "จุดแทรกแซงที่เหมาะสม", status: "unavailable", source_name: "HEAT//SHIFT" },
  { id: "historical", name_th: "การเปลี่ยนแปลงย้อนหลัง", unit: "°C/ปี", status: "unavailable", source_name: "Landsat 8/9" },
  { id: "future-risk", name_th: "สถานการณ์ความเสี่ยงอนาคต", unit: "°C change", status: "unavailable", source_name: "HEAT//SHIFT scenario model" },
];

export const emptyHistory: HistoryPoint[] = [];
