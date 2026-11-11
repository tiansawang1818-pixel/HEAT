"use client";

import Link from "next/link";
import { DataReadinessCard } from "@/components/data-readiness-card";
import { confirmBoundariesFromBrowser } from "@/lib/boundary-check";
import { HistoricalIngestionPanel } from "@/components/historical-ingestion-panel";
import { subscribeDatasetUpdates } from "@/lib/dataset-updates";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Activity,
  BarChart3,
  Bot,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  CloudSun,
  Database,
  Download,
  Droplets,
  Expand,
  FlaskConical,
  History,
  Layers3,
  Leaf,
  Map as MapIcon,
  Menu,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  ThermometerSun,
  TreePine,
  TrendingUp,
  Wind,
  X,
} from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { HeatMap, type MapViewport } from "@/components/heat-map";
import { StatusDot } from "@/components/status-dot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  askAssistant,
  approveCandidate,
  osmCandidateUrl,
  compareScenarios,
  createCostAssumption,
  downloadSavedScenarioReport,
  downloadOptimizationReport,
  loadAreaDetail,
  loadAdminSession,
  logoutAdmin,
  loadAreaMapFeature,
  loadAreaHistory,
  loadCostAssumptions,
  loadDashboardData,
  loadDatasetVersions,
  loadEvidencePendingCandidates,
  loadIngestionStatus,
  cityExtentBbox,
  loadLayerFeatures,
  loadMapObservations,
  loadRasterTileDescriptor,
  loadObservatoryStatus,
  loadProductionReadiness,
  loadPriorityRankings,
  loadSavedScenario,
  loadSavedScenarios,
  loadOptimizationRun,
  runForecastScenario,
  runOptimization,
  runPlanningEstimate,
  searchAreaDirectory,
  saveScenario,
  streamUrl,
  triggerRefresh,
} from "@/lib/api";
import { defaultLayers, demoDatasets, demoFeatures, emptyHistory, isDemoFallbackEnabled } from "@/lib/demo-data";
import {
  boundaryQualityLabel,
  cn,
  formatBaht,
  formatNumber,
  formatPopulationReferenceYear,
  formatThaiDate,
  freshnessLabel,
  isSyntheticQuality,
  populationColorScale,
  priorityComponentLabels,
  type AreaProperties,
  type AdminSessionStatus,
  type AreaDirectoryItem,
  type CandidateReviewRecord,
  type CostAssumptionRecord,
  type DatasetStatus,
  type DatasetVersionHistory,
  type FeatureCollection,
  type ForecastResult,
  type GeoFeature,
  type HistoryPoint,
  type MapLayerMeta,
  type MapObservation,
  type ObservatorySnapshot,
  type OptimizationResult,
  type PriorityRankingResult,
  type ProductionReadiness,
  type RasterTileDescriptor,
  type SavedScenarioRecord,
  type WeatherContext,
} from "@/lib/utils";

export type AppView = "explorer" | "insights" | "optimizer" | "scenarios" | "outlook" | "observatory" | "methodology";

const navigation: Array<{ id: AppView; label: string; href: string; icon: typeof MapIcon }> = [
  { id: "explorer", label: "แผนที่เมือง", href: "/", icon: MapIcon },
  { id: "insights", label: "วิเคราะห์ความเสี่ยง", href: "/insights", icon: BarChart3 },
  { id: "optimizer", label: "จัดสรรงบเมือง", href: "/optimizer", icon: CircleDollarSign },
  { id: "scenarios", label: "เปรียบเทียบแผน", href: "/scenarios", icon: FlaskConical },
  { id: "outlook", label: "ภาพอนาคต", href: "/outlook", icon: TrendingUp },
  { id: "observatory", label: "ศูนย์ข้อมูล", href: "/observatory", icon: Database },
  { id: "methodology", label: "วิธีการ", href: "/methodology", icon: ShieldCheck },
];

const layerIcons: Record<string, typeof ThermometerSun> = {
  temperature: ThermometerSun,
  priority: Activity,
  vegetation: TreePine,
  ndvi: Leaf,
  population: BarChart3,
  built: Layers3,
  built_up: Layers3,
  candidates: Sparkles,
  historical: History,
  "future-risk": TrendingUp,
};

const layerColors: Record<string, string> = {
  temperature: "#ff5a36",
  priority: "#ffb32c",
  vegetation: "#56e39f",
  ndvi: "#76dd71",
  population: "#56b8ff",
  built: "#aa8cff",
  built_up: "#aa8cff",
  candidates: "#4eeaff",
  historical: "#ff8b6d",
  "future-risk": "#ff5a36",
};

const interventionOptions = [
  { id: "tree_planting", label: "ปลูกต้นไม้" },
  { id: "public_shade_structure", label: "โครงสร้างบังแดด" },
  { id: "shaded_walkway", label: "ทางเดินร่มเงา" },
  { id: "cooling_rest_area", label: "จุดพักคลายร้อน" },
  { id: "reflective_surface", label: "พื้นผิวสะท้อนความร้อน" },
];

const viewCopy: Record<AppView, { eyebrow: string; title: string }> = {
  explorer: { eyebrow: "CITY EXPLORER", title: "สำรวจความร้อนของกรุงเทพฯ" },
  insights: { eyebrow: "HEAT EXPOSURE", title: "มองเห็นพื้นที่ที่ควรได้รับความสำคัญ" },
  optimizer: { eyebrow: "CITY OPTIMIZER", title: "เปลี่ยนงบประมาณให้เป็นพอร์ตลงทุน" },
  scenarios: { eyebrow: "SCENARIO LAB", title: "เทียบยุทธศาสตร์ด้วยเงื่อนไขเดียวกัน" },
  outlook: { eyebrow: "FUTURE OUTLOOK", title: "แยกข้อมูลอดีตออกจากฉากทัศน์อนาคต" },
  observatory: { eyebrow: "DATA OBSERVATORY", title: "ติดตามทุกแหล่งข้อมูลและงานประมวลผล" },
  methodology: { eyebrow: "METHODOLOGY", title: "หลักฐาน สมมติฐาน และข้อจำกัดที่ตรวจสอบได้" },
};

interface DashboardState {
  datasets: DatasetStatus[];
  layers: MapLayerMeta[];
  features: FeatureCollection;
  weather: WeatherContext | null;
  isDemo: boolean;
}

const initialState: DashboardState = {
  datasets: demoDatasets,
  layers: defaultLayers,
  features: isDemoFallbackEnabled() ? demoFeatures : { type: "FeatureCollection", features: [] },
  weather: null,
  isDemo: isDemoFallbackEnabled(),
};

function Metric({ label, value, suffix, tone = "default" }: { label: string; value: string; suffix?: string; tone?: "default" | "hot" | "green" }) {
  return (
    <div className="metric-card">
      <p>{label}</p>
      <div className={cn("metric-value", tone === "hot" && "text-thermal", tone === "green" && "text-emerald")}>
        {value}<span>{suffix}</span>
      </div>
    </div>
  );
}

function LoadingLine() {
  return <span className="inline-block h-3 w-20 animate-pulse rounded bg-white/10" />;
}

export function HeatCommandCenter({ initialView = "explorer" }: { initialView?: AppView }) {
  const [data, setData] = useState<DashboardState>(initialState);
  const [loading, setLoading] = useState(true);
  const [activeLayer, setActiveLayer] = useState(initialView === "outlook" ? "future-risk" : "priority");
  const [snapshotChoice, setSnapshotChoice] = useState<{ layer: string; id: string; observation?: MapObservation } | null>(null);
  const [observations, setObservations] = useState<{ layer: string; items: MapObservation[]; supports_snapshots: boolean } | null>(null);
  const [observationsLoading, setObservationsLoading] = useState(false);
  const [observationsError, setObservationsError] = useState<string | null>(null);
  const [rasterTile, setRasterTile] = useState<RasterTileDescriptor | null>(null);
  const [mainViewport, setMainViewport] = useState<MapViewport>({
    center: [100.5018, 13.7563],
    zoom: 10.4,
    bearing: 0,
    pitch: 0,
    bounds: [100.36, 13.59, 100.66, 13.91],
  });
  const [dataRevision, setDataRevision] = useState(0);
  const [selected, setSelected] = useState<GeoFeature | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>(emptyHistory);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<AreaDirectoryItem[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [connection, setConnection] = useState<"connecting" | "live" | "polling">("connecting");
  const [updateNotice, setUpdateNotice] = useState<string | null>(null);
  const [mobilePanel, setMobilePanel] = useState<"nav" | "analysis" | null>(null);
  const [rightTab, setRightTab] = useState<"area" | "optimizer" | "assistant">(
    initialView === "optimizer" || initialView === "scenarios" ? "optimizer" : "area",
  );
  const [bottomOpen, setBottomOpen] = useState(initialView !== "observatory");
  const [budget, setBudget] = useState(5_000_000);
  const [strategy, setStrategy] = useState(initialView === "scenarios" ? "optimized" : "balanced");
  const [interventions, setInterventions] = useState(interventionOptions.map((item) => item.id));
  const [optimizing, setOptimizing] = useState(false);
  const [exportingReport, setExportingReport] = useState(false);
  const [optimization, setOptimization] = useState<OptimizationResult | null>(null);
  const [scenarioResults, setScenarioResults] = useState<OptimizationResult[]>([]);
  const [activeScenarioId, setActiveScenarioId] = useState<string | null>(null);
  const [scenarioName, setScenarioName] = useState("แผนเปรียบเทียบ HEAT//SHIFT");
  const [savedScenario, setSavedScenario] = useState<SavedScenarioRecord | null>(null);
  const [savedScenarioList, setSavedScenarioList] = useState<SavedScenarioRecord[]>([]);
  const [savedScenariosLoading, setSavedScenariosLoading] = useState(initialView === "scenarios");
  const [openingScenarioId, setOpeningScenarioId] = useState<string | null>(null);
  const [savingScenario, setSavingScenario] = useState(false);
  const [exportingScenario, setExportingScenario] = useState(false);
  const [refreshJob, setRefreshJob] = useState<{ job_id: string; status: string } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [featuresLayer, setFeaturesLayer] = useState<string | null>(null);
  const mapNeedsRetry = useRef(false);
  const [assistantQuestion, setAssistantQuestion] = useState("");
  const [assistantAnswer, setAssistantAnswer] = useState<string | null>(null);
  const [assistantOptimization, setAssistantOptimization] = useState<OptimizationResult | null>(null);
  const [assistantLoading, setAssistantLoading] = useState(false);
  const [observatory, setObservatory] = useState<ObservatorySnapshot | null>(null);
  const [productionReadiness, setProductionReadiness] = useState<ProductionReadiness | null>(null);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [observatoryLoading, setObservatoryLoading] = useState(initialView === "observatory");
  const [forecastHorizon, setForecastHorizon] = useState<1 | 3 | 5>(5);
  const [forecastResult, setForecastResult] = useState<ForecastResult | null>(null);
  const [forecasting, setForecasting] = useState(false);
  const [priorityRankings, setPriorityRankings] = useState<PriorityRankingResult | null>(null);
  const [rankingsLoading, setRankingsLoading] = useState(initialView === "insights");

  const reload = useCallback(async (notify = false, metadataOnly = false, refreshActiveLayer = true) => {
    try {
      const next = await loadDashboardData(!metadataOnly);
      setData((current) => {
        if (metadataOnly) {
          return {
            ...next,
            features: current.features,
            isDemo: next.isDemo ? current.isDemo : false,
          };
        }
        return !current.isDemo && !next.isDemo ? { ...next, features: current.features } : next;
      });
      if (refreshActiveLayer) setDataRevision((value) => value + 1);
      if (notify) setUpdateNotice("เผยแพร่ข้อมูลเวอร์ชันใหม่แล้ว — แผนที่อัปเดตโดยไม่เปลี่ยนมุมมอง");
    } finally {
      setLoading(false);
    }
  }, []);

  const reloadOptimizationStatus = useCallback(async () => {
    if (optimization?.run_id && optimization.mode !== "planning_estimate") {
      try {
        const latest = await loadOptimizationRun(optimization.run_id);
        setOptimization({ ...latest, strategy: optimization.strategy });
      } catch {
        // Keep the historical result visible if status synchronization is temporarily unavailable.
      }
    }
    if (savedScenario?.id) {
      try {
        const latest = await loadSavedScenario(savedScenario.id);
        setSavedScenario(latest);
        setScenarioResults(latest.scenarios);
      } catch {
        // Keep the saved historical comparison visible if synchronization is temporarily unavailable.
      }
    }
    if (initialView === "scenarios") {
      try {
        setSavedScenarioList(await loadSavedScenarios());
      } catch {
        // The active comparison remains usable when the saved-scenario library is temporarily unavailable.
      }
    }
  }, [initialView, optimization?.run_id, optimization?.strategy, savedScenario?.id]);

  useEffect(() => {
    return subscribeDatasetUpdates({
      url: streamUrl(), activeLayer, reload, refreshPlans: reloadOptimizationStatus,
      connection: setConnection, needsMapRetry: () => mapNeedsRetry.current,
      candidateApproved: () => setUpdateNotice("โครงการผ่านหลักฐานแล้ว — ชั้นจุดลงทุนและสถานะแผนถูกอัปเดต"),
    });
  }, [activeLayer, reload, reloadOptimizationStatus]);

  const activeLayerVersion = data.layers.find((layer) => layer.id === activeLayer)?.layer_version;
  const layerObservations = observations?.layer === activeLayer ? observations : null;
  const selectedSnapshotId = snapshotChoice?.layer === activeLayer ? snapshotChoice.id : "";
  const selectedSnapshot = layerObservations?.items.find((item) => item.id === selectedSnapshotId) || (snapshotChoice?.layer === activeLayer ? snapshotChoice.observation : undefined);
  // Without an explicit date the API serves the current view: each district's
  // latest clear value and a mosaic of recent scenes. Pinning the newest scene
  // here would limit both to the part of Bangkok that one scene covers.
  const activeSnapshotId = selectedSnapshotId || undefined;
  // Only point layers need viewport filtering; district layers load the whole city once.
  const viewportFiltered = activeLayer === "candidates";
  const viewportBoundsKey = viewportFiltered
    ? mainViewport.bounds?.map((value) => value.toFixed(4)).join(",") || "default"
    : "city";

  useEffect(() => {
    let cancelled = false;
    setObservationsLoading(true);
    setObservationsError(null);
    void loadMapObservations(activeLayer).then((result) => {
      if (!cancelled) setObservations({ layer: activeLayer, ...result });
    }).catch(() => {
      if (!cancelled) setObservationsError("โหลดวันที่สังเกตการณ์ไม่สำเร็จ");
    }).finally(() => { if (!cancelled) setObservationsLoading(false); });
    return () => { cancelled = true; };
  }, [activeLayer, data.layers]);

  useEffect(() => {
    if (data.isDemo) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void loadLayerFeatures(activeLayer, viewportFiltered ? mainViewport.bounds : cityExtentBbox, controller.signal, activeLayerVersion, dataRevision, activeSnapshotId)
        .then((features) => {
          setData((current) => ({ ...current, features }));
          setFeaturesLayer(activeLayer);
          mapNeedsRetry.current = false;
          setMapError(null);
        })
        .catch((error) => {
          if (error instanceof DOMException && error.name === "AbortError") return;
          mapNeedsRetry.current = true;
          setMapError("โหลดชั้นข้อมูลไม่สำเร็จ ระบบจะลองใหม่และคงแผนที่เดิมไว้");
        });
    }, 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [activeLayer, activeLayerVersion, activeSnapshotId, data.isDemo, dataRevision, viewportBoundsKey]);

  useEffect(() => {
    if (data.isDemo) return;
    setRasterTile(null);
    let cancelled = false;
    loadRasterTileDescriptor(activeLayer, activeSnapshotId)
      .then((descriptor) => {
        if (!cancelled) setRasterTile(descriptor);
      })
      .catch(() => {
        if (!cancelled) setRasterTile(null);
      });
    return () => { cancelled = true; };
  }, [activeLayer, activeLayerVersion, activeSnapshotId, data.isDemo, dataRevision]);

  useEffect(() => {
    if (!updateNotice) return;
    const timer = setTimeout(() => setUpdateNotice(null), 7000);
    return () => clearTimeout(timer);
  }, [updateNotice]);

  useEffect(() => {
    if (!refreshJob || ["completed", "failed"].includes(refreshJob.status)) return;
    const timer = setInterval(() => {
      void loadIngestionStatus(refreshJob.job_id)
        .then(({ job }) => {
          if (!job) return;
          setRefreshJob({ job_id: job.id, status: job.status });
          if (job.status === "completed") void reload(true, true, true);
          if (job.status === "failed" && job.error_message) setActionError(job.error_message);
        })
        .catch(() => undefined);
    }, 2_000);
    return () => clearInterval(timer);
  }, [refreshJob, reload]);

  useEffect(() => {
    if (initialView !== "observatory") return;
    let cancelled = false;
    const refresh = () => {
      void loadObservatoryStatus()
        .then((snapshot) => {
          if (!cancelled) setObservatory(snapshot);
        })
        .catch(() => undefined)
        .finally(() => {
          if (!cancelled) setObservatoryLoading(false);
        });
      void loadProductionReadiness()
        .then((readiness) => {
          if (!cancelled) {
            setProductionReadiness(readiness);
            setReadinessError(null);
          }
        })
        .catch(() => {
          if (!cancelled) setReadinessError("ตรวจความพร้อมระบบไม่สำเร็จ ระบบจะลองใหม่อัตโนมัติ");
        });
    };
    refresh();
    const timer = setInterval(refresh, 10_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [initialView]);

  useEffect(() => {
    if (initialView !== "insights") return;
    let cancelled = false;
    void loadPriorityRankings()
      .then((rankings) => {
        if (!cancelled) setPriorityRankings(rankings);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setRankingsLoading(false);
      });
    return () => { cancelled = true; };
  }, [initialView]);

  useEffect(() => {
    if (initialView !== "scenarios") return;
    let cancelled = false;
    void loadSavedScenarios()
      .then((items) => {
        if (!cancelled) setSavedScenarioList(items);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setSavedScenariosLoading(false);
      });
    return () => { cancelled = true; };
  }, [initialView]);

  useEffect(() => {
    const normalized = query.trim();
    if (!normalized) {
      setSearchResults([]);
      setSearchLoading(false);
      return;
    }
    if (data.isDemo) {
      const lowered = normalized.toLocaleLowerCase("th-TH");
      setSearchResults(
        data.features.features
          .filter((feature) => `${feature.properties.name_th} ${feature.properties.name_en || ""}`.toLocaleLowerCase("th-TH").includes(lowered))
          .slice(0, 10)
          .map((feature) => ({
            id: feature.properties.id,
            name_th: feature.properties.name_th,
            name_en: feature.properties.name_en,
            quality_status: feature.properties.quality_status,
          })),
      );
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setSearchLoading(true);
    const timer = setTimeout(() => {
      void searchAreaDirectory(normalized, controller.signal)
        .then((items) => {
          if (!cancelled) setSearchResults(items);
        })
        .catch((error) => {
          if (!cancelled && !(error instanceof DOMException && error.name === "AbortError")) setSearchResults([]);
        })
        .finally(() => {
          if (!cancelled) setSearchLoading(false);
        });
    }, 220);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [data.isDemo, query]);

  const handleSelect = useCallback((feature: GeoFeature) => {
    setSelected(feature);
    setRightTab("area");
    setMobilePanel("analysis");
  }, []);

  const selectedAreaId = selected?.properties.id;
  useEffect(() => {
    if (!selectedAreaId) return;
    let cancelled = false;
    setHistoryLoading(true);
    setDetailLoading(true);
    loadAreaDetail(selectedAreaId, activeSnapshotId)
      .then((detail) => {
        if (!cancelled) setSelected((current) => current?.properties.id === selectedAreaId ? { ...current, properties: { ...current.properties, ...detail } } : current);
      })
      .catch(() => undefined)
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    loadAreaHistory(selectedAreaId, activeSnapshotId)
      .then((points) => { if (!cancelled) setHistory(points); })
      .catch(() => { if (!cancelled) setHistory([]); })
      .finally(() => { if (!cancelled) setHistoryLoading(false); });
    return () => { cancelled = true; };
  }, [selectedAreaId, activeSnapshotId, data.layers]);

  const handleSearchResult = useCallback(async (area: AreaDirectoryItem) => {
    try {
      const existing = data.features.features.find((feature) => feature.properties.id === area.id);
      const feature = existing || await loadAreaMapFeature(area.id);
      handleSelect(feature);
      if (area.centroid?.type === "Point") {
        const [longitude, latitude] = area.centroid.coordinates;
        if (typeof longitude === "number" && typeof latitude === "number") {
          setMainViewport((current) => ({ ...current, center: [longitude, latitude], zoom: Math.max(12, current.zoom) }));
        }
      }
      setQuery("");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "เปิดพื้นที่ไม่สำเร็จ");
    }
  }, [data.features.features, handleSelect]);

  const newestObservation = useMemo(() => {
    const valid = data.datasets.map((item) => item.last_source_observed_at).filter(Boolean) as string[];
    return valid.sort().at(-1) || null;
  }, [data.datasets]);

  const overallStatus = useMemo(() => {
    if (data.datasets.some((item) => item.status === "error")) return "error" as const;
    if (data.datasets.some((item) => item.status === "updating")) return "updating" as const;
    if (data.datasets.some((item) => item.status === "stale")) return "stale" as const;
    if (data.datasets.some((item) => item.status === "up_to_date")) return "up_to_date" as const;
    return "unavailable" as const;
  }, [data.datasets]);

  async function handleOptimize(compare = false, planning = false) {
    setOptimizing(true);
    setActionError(null);
    try {
      if (planning) {
        setOptimization(await runPlanningEstimate({ budget_thb: budget, strategy, intervention_types: interventions }));
        setBottomOpen(true);
      } else if (compare) {
        const results = await compareScenarios({ budget_thb: budget, intervention_types: interventions });
        setScenarioResults(results);
        setActiveScenarioId(results.at(-1)?.run_id || null);
        setSavedScenario(null);
      } else {
        const result = await runOptimization({ budget_thb: budget, strategy, intervention_types: interventions });
        setOptimization(result);
        setBottomOpen(true);
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "ไม่สามารถเรียก optimizer ได้");
    } finally {
      setOptimizing(false);
    }
  }

  async function handleRefresh(source: string, operatorToken: string) {
    setRefreshing(true);
    setActionError(null);
    try {
      setRefreshJob(await triggerRefresh(source, operatorToken));
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "เริ่มงานไม่ได้");
    } finally {
      setRefreshing(false);
    }
  }

  async function handleAssistant(question = assistantQuestion) {
    if (!question.trim()) return;
    setAssistantQuestion(question);
    setAssistantLoading(true);
    setActionError(null);
    try {
      const result = await askAssistant(question, selected?.properties.id);
      setAssistantAnswer(result.answer);
      setAssistantOptimization(result.optimization);
      if (result.optimization) {
        setOptimization(result.optimization);
        setBottomOpen(true);
        setUpdateNotice("OR-Tools คำนวณพอร์ตจาก candidate ที่มีหลักฐานแล้ว — แสดงตำแหน่งบนแผนที่");
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "ผู้ช่วยยังไม่พร้อมใช้งาน");
    } finally {
      setAssistantLoading(false);
    }
  }

  async function handleExportReport() {
    if (!optimization?.run_id || optimization.mode === "planning_estimate") return;
    setExportingReport(true);
    setActionError(null);
    try {
      await downloadOptimizationReport(optimization.run_id);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "ส่งออกรายงานไม่สำเร็จ");
    } finally {
      setExportingReport(false);
    }
  }

  async function handleSaveScenario() {
    if (!scenarioName.trim() || !scenarioResults.length) return;
    setSavingScenario(true);
    setActionError(null);
    try {
      const saved = await saveScenario({
        name: scenarioName.trim(),
        budget_thb: budget,
        intervention_types: interventions,
      });
      setSavedScenario(saved);
      setSavedScenarioList((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      setScenarioResults(saved.scenarios);
      setActiveScenarioId(saved.scenarios.at(-1)?.run_id || null);
      setUpdateNotice("บันทึกฉากทัศน์พร้อม dataset versions และผลเปรียบเทียบแล้ว");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "บันทึกฉากทัศน์ไม่สำเร็จ");
    } finally {
      setSavingScenario(false);
    }
  }

  async function handleOpenSavedScenario(scenarioId: string) {
    setOpeningScenarioId(scenarioId);
    setActionError(null);
    try {
      const saved = await loadSavedScenario(scenarioId);
      setSavedScenario(saved);
      setBudget(saved.budget_thb);
      if (saved.enabled_types.length) setInterventions(saved.enabled_types);
      setScenarioResults(saved.scenarios);
      setActiveScenarioId(saved.scenarios.at(-1)?.run_id || null);
      setUpdateNotice(saved.is_outdated ? "เปิดผลเดิมแล้ว — มี dataset version ใหม่กว่า ควรคำนวณใหม่ก่อนตัดสินใจ" : "เปิดฉากทัศน์ที่บันทึกไว้แล้ว");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "เปิดฉากทัศน์ที่บันทึกไว้ไม่สำเร็จ");
    } finally {
      setOpeningScenarioId(null);
    }
  }

  async function handleExportScenario() {
    if (!savedScenario) return;
    setExportingScenario(true);
    setActionError(null);
    try {
      await downloadSavedScenarioReport(savedScenario.id);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "ส่งออกรายงานฉากทัศน์ไม่สำเร็จ");
    } finally {
      setExportingScenario(false);
    }
  }

  async function handleForecast() {
    setForecasting(true);
    setActionError(null);
    setActiveLayer("future-risk");
    try {
      setForecastResult(await runForecastScenario(forecastHorizon));
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "ยังไม่สามารถคำนวณฉากทัศน์ได้");
    } finally {
      setForecasting(false);
    }
  }

  const currentLayer = data.layers.find((layer) => layer.id === activeLayer) || defaultLayers.find((layer) => layer.id === activeLayer);
  const activeScenario = scenarioResults.find((item) => item.run_id === activeScenarioId) || scenarioResults.at(-1);
  const displayedProjects = initialView === "scenarios" && activeScenario ? activeScenario.allocations : optimization?.allocations;
  // Until the active layer's features arrive, the map still holds the previous
  // layer's values; show it as loading rather than as missing data.
  const layerLoading = !data.isDemo && featuresLayer !== activeLayer;
  const displayedFeatures = useMemo<FeatureCollection>(() => {
    if (initialView !== "outlook" || !forecastResult?.projections.length) return data.features;
    const projections = new Map(forecastResult.projections.map((item) => [item.area_id, item]));
    return {
      type: "FeatureCollection",
      features: data.features.features.map((feature) => {
        const projection = projections.get(feature.properties.id);
        if (!projection) return feature;
        return {
          ...feature,
          properties: {
            ...feature.properties,
            scenario_change_c: projection.scenario_change_c,
            scenario_uncertainty_c: projection.uncertainty_c,
            scenario_horizon_years: projection.horizon_years,
            quality_status: "scenario_projection",
          },
        };
      }),
    };
  }, [data.features, forecastResult, initialView]);
  const containsSynthetic = data.features.features.some((feature) =>
    isSyntheticQuality(feature.properties.quality_status) || isSyntheticQuality(feature.properties.boundary_quality_status)
  ) || isSyntheticQuality(selected?.properties.quality_status);

  return (
    <main className="command-center">
      <header className="topbar">
        <div className="brand-block">
          <button className="mobile-menu" onClick={() => setMobilePanel(mobilePanel === "nav" ? null : "nav")} aria-label="เปิดเมนู">
            <Menu size={18} />
          </button>
          <Link href="/" className="brand-mark" aria-label="HEAT SHIFT หน้าหลัก">
            <span>HEAT</span><i>//</i><span>SHIFT</span>
          </Link>
          <div className="brand-tagline">DON&apos;T JUST MAP THE HEAT.<br /><strong>CHANGE THE CITY.</strong></div>
        </div>
        <div className="topbar-statuses">
          <div className="top-status">
            <span>ข้อมูลล่าสุดจากแหล่งต้นทาง</span>
            <strong>{formatThaiDate(newestObservation, true)}</strong>
          </div>
          <div className="top-status">
            <span>สถานะข้อมูล</span>
            <StatusDot status={overallStatus} />
          </div>
          <div className="top-status">
            <span>การเชื่อมต่อ</span>
            <strong className={connection === "live" ? "text-emerald" : "text-amber-300"}>
              {connection === "live" ? "SSE เชื่อมต่อแล้ว" : connection === "polling" ? "สำรองด้วย polling" : "กำลังเชื่อมต่อ"}
            </strong>
          </div>
        </div>
        <Button variant="outline" size="icon" aria-label="เต็มหน้าจอ" onClick={() => document.documentElement.requestFullscreen?.()}>
          <Expand size={15} />
        </Button>
      </header>

      {(data.isDemo || containsSynthetic) && (
        <div className="demo-banner" role="status">
          <ShieldCheck size={14} />
          <strong>{data.isDemo ? "โหมดสาธิตเฉพาะโครง UX" : "มีข้อมูลสังเคราะห์สำหรับทดสอบ"}</strong>
          <span>{data.isDemo ? "backend หรือชุดข้อมูลจริงยังไม่พร้อม — รูปร่างบนแผนที่เป็นพื้นที่ตัวอย่างและไม่มีค่าการวัดสมมติ" : "ข้อมูลที่ระบุ synthetic/demo ใช้ตรวจการทำงานของระบบเท่านั้น ไม่ใช่การสังเกตจริง"}</span>
        </div>
      )}

      <aside className={cn("left-sidebar", mobilePanel === "nav" && "mobile-open")}>
        <div className="sidebar-head mobile-only">
          <span>เมนูหลัก</span><button onClick={() => setMobilePanel(null)}><X size={17} /></button>
        </div>
        <nav className="primary-nav" aria-label="ส่วนต่าง ๆ ของแอป">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <Link key={item.id} href={item.href} className={cn(initialView === item.id && "active")}>
                <Icon size={15} /><span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-section search-section">
          <label htmlFor="area-search">ค้นหาพื้นที่</label>
          <div className="search-box"><Search size={14} /><input id="area-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="เขต / แขวง" /></div>
          {query && (
            <div className="search-results">
              {searchResults.slice(0, 5).map((area) => (
                <button key={area.id} onClick={() => void handleSearchResult(area)}>{area.name_th}</button>
              ))}
              {searchLoading && <p>กำลังค้นหา…</p>}
              {!searchLoading && !searchResults.length && <p>ไม่พบพื้นที่</p>}
            </div>
          )}
        </div>
        <div className="sidebar-section layer-section">
          <div className="section-label"><span>ชั้นข้อมูล</span><Layers3 size={13} /></div>
          <div className="layer-list">
            {data.layers.map((layer) => {
              const Icon = layerIcons[layer.id] || Layers3;
              return (
                <button key={layer.id} onClick={() => setActiveLayer(layer.id)} className={cn("layer-button", activeLayer === layer.id && "active")}>
                  <span className="layer-icon" style={{ color: layerColors[layer.id] || "#cbd5e1" }}><Icon size={15} /></span>
                  <span className="layer-name">{layer.name_th}<small>{layer.source_name || "ไม่ระบุแหล่ง"}</small></span>
                  <StatusDot status={layer.status} label={false} />
                </button>
              );
            })}
          </div>
        </div>
        <div className="sidebar-section date-section">
          <div className="section-label"><span>{activeLayer === "population" ? "ปีอ้างอิงประชากร" : "วันที่สังเกตการณ์"}</span><Clock3 size={13} /></div>
          {layerObservations?.supports_snapshots ? (
            <select className="date-select" aria-label={activeLayer === "population" ? "เลือกปีอ้างอิงประชากร" : "เลือกวันที่สังเกตการณ์"} value={selectedSnapshotId} disabled={observationsLoading || !layerObservations.items.length} onChange={(event) => setSnapshotChoice({ layer: activeLayer, id: event.target.value, observation: layerObservations.items.find((item) => item.id === event.target.value) })}>
              <option value="">{layerObservations.items.length ? "ข้อมูลล่าสุดที่ผ่านการตรวจสอบ" : "ยังไม่มี observation ที่เผยแพร่"}</option>
              {layerObservations.items.map((item) => <option key={item.id} value={item.id}>{activeLayer === "population" ? formatPopulationReferenceYear(item.observed_at) : formatThaiDate(item.observed_at, true)} · v{item.layer_version}</option>)}
            </select>
          ) : <div className="date-select">{activeLayer === "population" ? formatPopulationReferenceYear(currentLayer?.observation_date) : formatThaiDate(currentLayer?.observation_date)}</div>}
          <p>{observationsError || (observationsLoading ? "กำลังอ่านวันที่จาก backend" : selectedSnapshot ? "กำลังดูข้อมูลย้อนหลัง ขอบเขตพื้นที่และแผนลงทุนอ้างอิงปัจจุบัน" : activeLayer === "population" ? "ค่าประมาณตามปีอ้างอิง ไม่ใช่การตรวจวัดรายวัน" : "แสดงวันที่ต้นทาง ไม่ใช่วันที่นำเข้าระบบ")}</p>
        </div>
      </aside>

      <section className="map-stage">
        {mapError && <div className="map-load-error error-box" role="status">{mapError}</div>}
        <HeatMap
          data={displayedFeatures}
          activeLayer={activeLayer}
          loading={layerLoading}
          rasterTile={rasterTile}
          viewport={mainViewport}
          onViewportChange={setMainViewport}
          selectedId={selected?.properties.id}
          projects={displayedProjects}
          onSelect={handleSelect}
        />
        {initialView === "scenarios" && scenarioResults.length > 0 && (
          <ScenarioComparisonMaps
            data={data.features}
            activeLayer={activeLayer}
            selectedId={selected?.properties.id}
            scenarios={scenarioResults}
            activeScenarioId={activeScenario?.run_id || null}
            onScenarioSelect={setActiveScenarioId}
            onAreaSelect={handleSelect}
          />
        )}
        {initialView === "outlook" && (
          <FutureOutlookOverlay
            horizon={forecastHorizon}
            setHorizon={setForecastHorizon}
            result={forecastResult}
            loading={forecasting}
            disabled={data.isDemo}
            error={actionError}
            areas={data.features}
            onRun={() => void handleForecast()}
            onBaseline={() => {
              setForecastResult(null);
              setActiveLayer("temperature");
            }}
          />
        )}
        {initialView === "insights" && (
          <InsightsOverlay
            rankings={priorityRankings}
            loading={rankingsLoading}
            onSelectArea={(areaId) => {
              const feature = data.features.features.find((item) => item.properties.id === areaId);
              if (feature) handleSelect(feature);
            }}
          />
        )}
        {initialView === "explorer" && data.weather?.current && (
          <WeatherContextCard weather={data.weather} />
        )}
        <div className="map-title-card">
          <span>{viewCopy[initialView].eyebrow}</span>
          <h1>{viewCopy[initialView].title}</h1>
          <p>ข้อมูลแต่ละชั้นเผยแพร่เมื่อผ่าน validation แล้วเท่านั้น</p>
        </div>
        <div className="legend-card">
          <div className="legend-heading"><span>{currentLayer?.name_th || activeLayer}</span><Badge>{currentLayer?.unit || "INDEX"}</Badge></div>
          <div
            className={cn("legend-ramp", !rasterTile && (activeLayer === "vegetation" || activeLayer === "ndvi") && "green")}
            style={rasterTile ? { background: `linear-gradient(90deg, ${rasterTile.legend.palette.map((color) => `#${color}`).join(", ")})` } : activeLayer === "population" ? { background: `linear-gradient(90deg, rgb(${populationColorScale.from.join(",")}), rgb(${populationColorScale.to.join(",")}))` } : undefined}
          />
          <div className="legend-labels">
            <span>{rasterTile ? rasterTile.legend.min : activeLayer === "population" ? populationColorScale.min : "ต่ำ"}</span>
            <span>{rasterTile ? rasterTile.legend.max : activeLayer === "population" ? `≥ ${formatNumber(populationColorScale.max)}` : "สูง"}</span>
          </div>
          <div className="legend-meta">{selectedSnapshot ? <Badge>ข้อมูลย้อนหลัง</Badge> : <StatusDot status={currentLayer?.status || "unavailable"} />}<span>v{selectedSnapshot?.layer_version ?? currentLayer?.layer_version ?? "—"}</span></div>
          <p className="legend-source-reason">{selectedSnapshot ? activeLayer === "population" ? formatPopulationReferenceYear(selectedSnapshot.observed_at) : `สังเกตการณ์ ${formatThaiDate(selectedSnapshot.observed_at, true)}` : currentLayer?.status_reason}</p>
        </div>
        <button className="mobile-analysis" onClick={() => setMobilePanel("analysis")}><Activity size={15} /> วิเคราะห์พื้นที่</button>
        <AnimatePresence>
          {updateNotice && (
            <motion.div className="update-toast" initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
              <RefreshCw size={15} />{updateNotice}
            </motion.div>
          )}
        </AnimatePresence>
        {initialView === "observatory" && (
          <ObservatoryOverlay
            datasets={data.datasets}
            snapshot={observatory}
            readiness={productionReadiness}
            readinessError={readinessError}
            loading={observatoryLoading}
            onRefresh={handleRefresh}
            refreshing={refreshing}
            job={refreshJob}
            error={actionError}
          />
        )}
        {initialView === "methodology" && <MethodologyOverlay />}
      </section>

      <aside className={cn("right-sidebar", mobilePanel === "analysis" && "mobile-open")}>
        <div className="sidebar-head">
          <div className="panel-tabs" role="tablist">
            <button className={cn(rightTab === "area" && "active")} onClick={() => setRightTab("area")}>พื้นที่</button>
            <button className={cn(rightTab === "optimizer" && "active")} onClick={() => setRightTab("optimizer")}>งบเมือง</button>
            <button className={cn(rightTab === "assistant" && "active")} onClick={() => setRightTab("assistant")}><Bot size={13} /> AI</button>
          </div>
          <button className="mobile-only" onClick={() => setMobilePanel(null)} aria-label="ปิดแผง"><X size={17} /></button>
        </div>
        <AnimatePresence mode="wait">
          {rightTab === "area" && (
            <motion.div key="area" className="panel-scroll" initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}>
              <AreaPanel area={selected?.properties || null} loading={loading} detailLoading={detailLoading} onOptimize={() => setRightTab("optimizer")} />
            </motion.div>
          )}
          {rightTab === "optimizer" && (
            <motion.div key="optimizer" className="panel-scroll" initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}>
              <OptimizerPanel
                budget={budget}
                setBudget={(value) => {
                  setBudget(value);
                  setScenarioResults([]);
                  setSavedScenario(null);
                }}
                strategy={strategy}
                setStrategy={setStrategy}
                interventions={interventions}
                setInterventions={(value) => {
                  setInterventions(value);
                  setScenarioResults([]);
                  setSavedScenario(null);
                }}
                onRun={() => void handleOptimize(false)}
                onCompare={() => void handleOptimize(true)}
                onEstimate={() => void handleOptimize(false, true)}
                onExport={() => void handleExportReport()}
                loading={optimizing}
                exporting={exportingReport}
                result={optimization}
                scenarios={scenarioResults}
                activeScenarioId={activeScenario?.run_id || null}
                onScenarioSelect={setActiveScenarioId}
                scenarioMode={initialView === "scenarios"}
                scenarioName={scenarioName}
                setScenarioName={setScenarioName}
                savedScenario={savedScenario}
                savedScenarioList={savedScenarioList}
                savedScenariosLoading={savedScenariosLoading}
                openingScenarioId={openingScenarioId}
                savingScenario={savingScenario}
                exportingScenario={exportingScenario}
                onSaveScenario={() => void handleSaveScenario()}
                onExportScenario={() => void handleExportScenario()}
                onOpenSavedScenario={(scenarioId) => void handleOpenSavedScenario(scenarioId)}
                disabled={data.isDemo}
                error={actionError}
              />
            </motion.div>
          )}
          {rightTab === "assistant" && (
            <motion.div key="assistant" className="panel-scroll" initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}>
              <AssistantPanel
                area={selected?.properties || null}
                question={assistantQuestion}
                setQuestion={setAssistantQuestion}
                answer={assistantAnswer}
                optimization={assistantOptimization}
                loading={assistantLoading}
                disabled={data.isDemo}
                onAsk={(question) => void handleAssistant(question)}
                error={actionError}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </aside>

      <section className={cn("bottom-panel", !bottomOpen && "collapsed")}>
        <button className="bottom-toggle" onClick={() => setBottomOpen((open) => !open)}>
          <History size={14} /> ประวัติและผลลัพธ์ <ChevronDown className={cn(bottomOpen && "rotate-180")} size={14} />
        </button>
        {bottomOpen && (
          <div className="bottom-content">
            <HistoryChart history={history} loading={historyLoading} areaName={selected?.properties.name_th} />
            <div className="bottom-summary">
              <div><span>แหล่งข้อมูลที่พร้อม</span><strong>{data.datasets.filter((item) => item.status === "up_to_date").length}<small> / {data.datasets.length}</small></strong></div>
              <div><span>ชั้นข้อมูลที่เลือก</span><strong className="text-cyan">{currentLayer?.name_th || "—"}</strong></div>
              <div><span>พอร์ตล่าสุด</span><strong>{optimization ? formatBaht(optimization.total_cost_thb) : "ยังไม่ได้คำนวณ"}</strong></div>
            </div>
          </div>
        )}
      </section>
    </main>
  );
}

function WeatherContextCard({ weather }: { weather: WeatherContext }) {
  const current = weather.current;
  if (!current) return null;
  const times = weather.hourly?.time || [];
  const reference = Date.parse(weather.reference_time || weather.last_source_observation || "");
  const forecastIndices = times
    .map((time, index) => ({ index, timestamp: Date.parse(asUtcWeatherTime(time)) }))
    .filter((item) => Number.isNaN(reference) || item.timestamp > reference)
    .slice(0, 3);
  return (
    <div className="weather-context-card" aria-label="บริบทอากาศจากแบบจำลอง Open-Meteo">
      <div className="weather-card-head">
        <span><CloudSun size={14} /> บริบทอากาศ</span>
        <StatusDot status={weather.status} label={false} />
      </div>
      <div className="weather-primary">
        <strong>{formatNumber(current.temperature_2m, 1)}<small>{weather.current_units.temperature_2m || "°C"}</small></strong>
        <span>รู้สึกเหมือน {formatNumber(current.apparent_temperature, 1)}{weather.current_units.apparent_temperature || "°C"}</span>
      </div>
      <div className="weather-metrics">
        <span><Droplets size={11} /> ความชื้น <strong>{formatNumber(current.relative_humidity_2m)}{weather.current_units.relative_humidity_2m || "%"}</strong></span>
        <span><Wind size={11} /> ลม <strong>{formatNumber(current.wind_speed_10m, 1)} {weather.current_units.wind_speed_10m || "km/h"}</strong></span>
      </div>
      {!!forecastIndices.length && (
        <div className="weather-forecast">
          {forecastIndices.map(({ index }) => (
            <div key={`${times[index]}-${index}`}>
              <time>{formatThaiDate(asUtcWeatherTime(times[index]), true)}</time>
              <strong>{formatNumber(weather.hourly?.temperature_2m?.[index], 1)}°</strong>
              <span>ฝน {formatNumber(weather.hourly?.precipitation_probability?.[index])}%</span>
            </div>
          ))}
        </div>
      )}
      <div className="weather-provenance">
        <span>อ้างอิง {formatThaiDate(weather.reference_time || weather.last_source_observation, true)} · v{weather.layer_version || 0}</span>
        <strong>แบบจำลองอากาศ/พยากรณ์ — ไม่ใช่ LST ดาวเทียม</strong>
      </div>
    </div>
  );
}

function asUtcWeatherTime(value: string) {
  return /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`;
}


function InsightsOverlay({ rankings, loading, onSelectArea }: {
  rankings: PriorityRankingResult | null;
  loading: boolean;
  onSelectArea: (areaId: string) => void;
}) {
  const items = rankings?.items || [];
  const top = items[0];
  return (
    <motion.div className="map-overlay-panel insights-overlay" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }}>
      <div className="overlay-heading"><div><Badge>HEAT EXPOSURE PRIORITY</Badge><h2>พื้นที่ที่ควรพิจารณาก่อน</h2><p>เรียงจากคะแนนล่าสุดของแต่ละพื้นที่ พร้อมแสดงข้อมูลที่ขาด</p></div></div>
      <div className="insights-warning"><ShieldCheck size={14} /><span>{rankings?.interpretation_warning_th || "คะแนนนี้ไม่ใช่ข้อสรุปเรื่องรายได้หรือความเหลื่อมล้ำทางเศรษฐกิจ"}</span></div>
      {loading && <div className="observatory-loading"><RefreshCw className="animate-spin" size={14} /> กำลังโหลดอันดับล่าสุด</div>}
      {!loading && !items.length && <div className="insights-empty"><Activity /><strong>ยังไม่มีคะแนนที่ผ่านการคำนวณ</strong><span>ระบบจะเผยแพร่อันดับเมื่อ input datasets ผ่าน validation และคำนวณสูตรสำเร็จ</span></div>}
      {top && (
        <Card className="top-priority-card">
          <div><span>อันดับสูงสุด</span><h3>{top.area_name_th}</h3><small>คำนวณ {formatThaiDate(top.calculated_at, true)} · {top.formula_version}</small></div>
          <strong>{formatNumber(top.score, 1)}<small>/100</small></strong>
          <div className="component-bars">
            {Object.entries(top.components).map(([key, value]) => (
              <div key={key}><span>{priorityComponentLabels[key] || key}</span><i><b style={{ width: `${Math.min(100, Math.max(0, Number(value || 0) * 100))}%` }} /></i><strong>{formatNumber(value, 2)}</strong></div>
            ))}
          </div>
        </Card>
      )}
      {!!items.length && (
        <div className="priority-ranking-list">
          {items.map((item) => (
            <button key={item.area_id} onClick={() => onSelectArea(item.area_id)}>
              <span className="rank-number">{String(item.rank).padStart(2, "0")}</span>
              <span className="rank-name">{item.area_name_th}<small>{item.missing_inputs.length ? `ขาด ${item.missing_inputs.length} ตัวแปร` : "ข้อมูลครบตามสูตร"}</small></span>
              <i><b style={{ width: `${Math.min(100, Math.max(0, item.score))}%` }} /></i>
              <strong>{formatNumber(item.score, 1)}</strong>
            </button>
          ))}
        </div>
      )}
      {!!items.length && <div className="insights-footer">แสดง {items.length} จาก {rankings?.total ?? items.length} พื้นที่ · คลิกเพื่อเปิดรายละเอียดบนแผนที่</div>}
    </motion.div>
  );
}

function FutureOutlookOverlay({
  horizon,
  setHorizon,
  result,
  loading,
  disabled,
  error,
  areas,
  onRun,
  onBaseline,
}: {
  horizon: 1 | 3 | 5;
  setHorizon: (value: 1 | 3 | 5) => void;
  result: ForecastResult | null;
  loading: boolean;
  disabled: boolean;
  error: string | null;
  areas: FeatureCollection;
  onRun: () => void;
  onBaseline: () => void;
}) {
  const names = useMemo(() => new Map(areas.features.map((feature) => [feature.properties.id, feature.properties.name_th])), [areas.features]);
  return (
    <motion.div className="map-overlay-panel future-outlook" initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}>
      <div className="overlay-heading">
        <div><Badge>SCENARIO · NOT A FORECAST</Badge><h2>ภาพความเสี่ยงระยะ 5 ปี</h2><p>ใช้ข้อมูลย้อนหลังที่ผ่าน validation และแสดง uncertainty ทุกครั้ง</p></div>
      </div>
      <div className="outlook-baseline">
        <button onClick={onBaseline}><History size={13} /><span>Current baseline<small>ข้อมูลสังเกตการณ์</small></span></button>
        {[1, 3, 5].map((year) => (
          <button className={cn(horizon === year && "active")} key={year} onClick={() => setHorizon(year as 1 | 3 | 5)}>
            +{year} ปี
          </button>
        ))}
      </div>
      <Button className="w-full" variant="thermal" onClick={onRun} disabled={loading || disabled}>
        {loading ? <RefreshCw className="animate-spin" size={15} /> : <TrendingUp size={15} />}
        {loading ? "กำลังคำนวณฉากทัศน์…" : `คำนวณฉากทัศน์ +${horizon} ปี`}
      </Button>
      {disabled && <div className="inline-warning">ต้องเชื่อม backend และข้อมูลย้อนหลังที่ผ่าน validation ก่อน</div>}
      {error && <div className="error-box">{error}</div>}
      {result?.status === "insufficient_data" && (
        <div className="outlook-guardrail">
          <ShieldCheck size={17} /><div><strong>ยังไม่เผยแพร่คำพยากรณ์</strong><p>{result.message_th}</p><small>เวลา {result.available_years ?? 0}/{result.required_years ?? 5} ปี · พื้นที่ผ่านเกณฑ์ {result.eligible_areas ?? 0}/{result.required_areas ?? 5}</small></div>
        </div>
      )}
      {result?.status === "completed" && (
        <>
          <div className="outlook-result-head"><span>ผลฉากทัศน์</span><strong>{result.projections.length} พื้นที่</strong></div>
          <div className="outlook-projections">
            {result.projections.slice(0, 12).map((projection) => (
              <div key={projection.area_id}>
                <span>{names.get(projection.area_id) || projection.area_id}</span>
                <strong>{projection.scenario_change_c >= 0 ? "+" : ""}{formatNumber(projection.scenario_change_c, 2)}°C</strong>
                <small>±{formatNumber(projection.uncertainty_c, 2)}°C</small>
              </div>
            ))}
          </div>
        </>
      )}
      {result && (
        <div className="outlook-provenance">
          <span>Method</span><strong>{result.method || "scenario method not reported"}</strong>
          <span>Model version</span><strong>{result.model_version || "not reported"}</strong>
          <span>Temporal holdout</span><strong>{result.metrics?.temporal_holdout_samples ?? "—"} samples · MAE {formatNumber(result.metrics?.temporal_model_mae_c as number | undefined, 2)}°C</strong>
          <span>Spatial holdout</span><strong>{result.metrics?.spatial_holdout_samples ?? "—"} samples · MAE {formatNumber(result.metrics?.spatial_model_mae_c as number | undefined, 2)}°C</strong>
          <span>สร้างเมื่อ</span><strong>{formatThaiDate(result.created_at, true)}</strong>
        </div>
      )}
    </motion.div>
  );
}

function ScenarioComparisonMaps({
  data,
  activeLayer,
  selectedId,
  scenarios,
  activeScenarioId,
  onScenarioSelect,
  onAreaSelect,
}: {
  data: FeatureCollection;
  activeLayer: string;
  selectedId?: string;
  scenarios: OptimizationResult[];
  activeScenarioId: string | null;
  onScenarioSelect: (runId: string) => void;
  onAreaSelect: (feature: GeoFeature) => void;
}) {
  const [viewport, setViewport] = useState<MapViewport>({ center: [100.5018, 13.7563], zoom: 10.4, bearing: 0, pitch: 0 });
  const synchronizeViewport = useCallback((next: MapViewport) => {
    setViewport((current) => {
      const unchanged = Math.abs(current.center[0] - next.center[0]) < 0.00001
        && Math.abs(current.center[1] - next.center[1]) < 0.00001
        && Math.abs(current.zoom - next.zoom) < 0.001
        && Math.abs(current.bearing - next.bearing) < 0.01
        && Math.abs(current.pitch - next.pitch) < 0.01;
      return unchanged ? current : next;
    });
  }, []);
  return (
    <motion.div className="scenario-map-grid" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
      {scenarios.map((scenario) => (
        <article className={cn("scenario-map-card", scenario.run_id === activeScenarioId && "active")} key={scenario.run_id}>
          <button className="scenario-map-header" onClick={() => onScenarioSelect(scenario.run_id)}>
            <span>{scenario.strategy}</span>
            <Badge>{scenario.allocations.length} โครงการ</Badge>
          </button>
          <div className="scenario-map-canvas">
            <HeatMap
              compact
              data={data}
              activeLayer={activeLayer}
              selectedId={selectedId}
              projects={scenario.allocations}
              viewport={viewport}
              onViewportChange={synchronizeViewport}
              onSelect={onAreaSelect}
              mapLabel={`แผนที่เปรียบเทียบ ${scenario.strategy}`}
            />
          </div>
          <div className="scenario-map-stats">
            <span>ใช้งบ <strong>{formatBaht(scenario.total_cost_thb)}</strong></span>
            <span>ผลรวมรายโครงการ <strong>{formatNumber(scenario.estimated_beneficiaries)}</strong></span>
          </div>
          {scenario.status === "insufficient_data" && <p className="inline-warning">{scenario.limitations?.join(" · ") || "หลักฐานยังไม่พอสำหรับยุทธศาสตร์นี้"}</p>}
        </article>
      ))}
      <div className="scenario-sync-note"><Activity size={12} /> เลื่อนหรือซูมแผนที่ใดก็ได้ — มุมมองทั้งสามจะซิงก์กัน</div>
    </motion.div>
  );
}

function AreaPanel({ area, loading, detailLoading = false, onOptimize }: { area: AreaProperties | null; loading: boolean; detailLoading?: boolean; onOptimize: () => void }) {
  if (loading) return <div className="empty-state"><Activity className="animate-pulse text-cyan" /><LoadingLine /><p>กำลังเชื่อมข้อมูลที่ผ่านการตรวจสอบ</p></div>;
  if (!area) return <div className="empty-state"><MapIcon /><h2>เลือกพื้นที่บนแผนที่</h2><p>คลิกเขตเพื่อดูข้อมูลจริง วันที่สังเกตการณ์ และเหตุผลของคะแนน</p></div>;
  const isDemo = isSyntheticQuality(area.quality_status);
  return (
    <>
      <div className="area-heading">
        <div><span>AREA ANALYSIS</span><h2>{area.name_th}</h2><p>{area.name_en}</p></div>
        <div className="score-ring"><strong>{formatNumber(area.priority_score)}</strong><span>/100</span></div>
      </div>
      {isDemo && <div className="inline-warning">ข้อมูลสาธิตหรือสังเคราะห์สำหรับทดสอบ ไม่ใช่การสังเกตจริง</div>}
      {detailLoading && <p className="readiness-scope"><RefreshCw className="inline animate-spin" size={12} /> กำลังโหลดรายละเอียดของเขตนี้…</p>}
      <div className="observation-strip"><Clock3 size={13} /><span>ข้อมูลสิ่งแวดล้อมล่าสุด</span><strong>{formatThaiDate(area.observation_date)}</strong></div>
      <div className="metrics-grid">
        <Metric label="อุณหภูมิพื้นผิว" value={formatNumber(area.surface_temperature_c, 1)} suffix={area.surface_temperature_c == null ? "" : "°C"} tone="hot" />
        <Metric label="ความต่างจากพื้นที่รอบข้าง" value={formatNumber(area.temperature_anomaly_c, 1)} suffix={area.temperature_anomaly_c == null ? "" : "°C"} />
        <Metric label="ประชากรโดยประมาณ" value={formatNumber(area.population)} suffix={area.population == null ? "" : " คน"} />
        <Metric label="เรือนยอดไม้" value={formatNumber(area.tree_coverage_pct, 1)} suffix={area.tree_coverage_pct == null ? "" : "%"} tone="green" />
        <Metric label="NDVI" value={formatNumber(area.ndvi, 2)} />
        <Metric label="สิ่งปลูกสร้าง" value={formatNumber(area.built_up_pct, 1)} suffix={area.built_up_pct == null ? "" : "%"} />
      </div>
      <div className="explanation-block">
        <div className="section-label"><span>เหตุผลของคะแนน</span><ShieldCheck size={13} /></div>
        {(area.priority_explanation?.length ? area.priority_explanation : ["ยังไม่มีคำอธิบายจาก backend"]).map((reason, index) => (
          <div className="reason-row" key={`${reason}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><p>{reason}</p></div>
        ))}
      </div>
      <div className="source-note">ประชากร: ค่าประมาณจากแบบจำลอง · ปีอ้างอิง {area.population_reference_year || "ยังไม่ระบุ"}<br />คุณภาพขอบเขต: {boundaryQualityLabel(area.quality_status)}</div>
      <Button className="w-full" variant="thermal" onClick={onOptimize} disabled={isDemo}><CircleDollarSign size={15} /> นำพื้นที่นี้ไปวิเคราะห์การลงทุน</Button>
    </>
  );
}

interface OptimizerPanelProps {
  budget: number;
  setBudget: (value: number) => void;
  strategy: string;
  setStrategy: (value: string) => void;
  interventions: string[];
  setInterventions: (value: string[]) => void;
  onRun: () => void;
  onCompare: () => void;
  onEstimate: () => void;
  onExport: () => void;
  loading: boolean;
  exporting: boolean;
  result: OptimizationResult | null;
  scenarios: OptimizationResult[];
  activeScenarioId: string | null;
  onScenarioSelect: (runId: string) => void;
  scenarioMode: boolean;
  scenarioName: string;
  setScenarioName: (value: string) => void;
  savedScenario: SavedScenarioRecord | null;
  savedScenarioList: SavedScenarioRecord[];
  savedScenariosLoading: boolean;
  openingScenarioId: string | null;
  savingScenario: boolean;
  exportingScenario: boolean;
  onSaveScenario: () => void;
  onExportScenario: () => void;
  onOpenSavedScenario: (scenarioId: string) => void;
  disabled: boolean;
  error: string | null;
}

function OptimizerPanel(props: OptimizerPanelProps) {
  const toggle = (id: string) => props.setInterventions(props.interventions.includes(id) ? props.interventions.filter((item) => item !== id) : [...props.interventions, id]);
  return (
    <>
      <div className="panel-title"><span>CITY BUDGET OPTIMIZER</span><h2>งบประมาณเพื่อคลายร้อน</h2><p>ตัวแก้ปัญหาเลือกโครงการภายใต้งบและข้อจำกัดความเหมาะสมจริง</p></div>
      <div className="budget-display"><span>งบลงทุนรวม</span><strong>{formatBaht(props.budget)}</strong></div>
      <input className="budget-range" type="range" min={100_000} max={100_000_000} step={100_000} value={props.budget} onChange={(event) => props.setBudget(Number(event.target.value))} />
      <div className="budget-bounds"><span>฿100,000</span><span>฿100M</span></div>
      <label className="field-label" htmlFor="exact-budget">ระบุงบประมาณ (บาท)</label>
      <input id="exact-budget" className="text-field" type="number" min={100_000} max={100_000_000} step={100_000} value={props.budget} onChange={(event) => props.setBudget(Math.min(100_000_000, Math.max(100_000, Number(event.target.value))))} />
      <div className="field-label">เป้าหมายหลัก</div>
      <div className="strategy-grid">
        {[
          ["balanced", "สมดุล"], ["beneficiaries", "ผู้รับประโยชน์"], ["equity", "พื้นที่ขาดแคลน"], ["cost_effective", "คุ้มค่าต่องบ"],
        ].map(([id, label]) => <button className={cn(props.strategy === id && "active")} key={id} onClick={() => props.setStrategy(id)}>{label}</button>)}
      </div>
      <div className="field-label">ประเภทโครงการ</div>
      <div className="checkbox-list">
        {interventionOptions.map((item) => (
          <label key={item.id}><input type="checkbox" checked={props.interventions.includes(item.id)} onChange={() => toggle(item.id)} /><span>{item.label}</span></label>
        ))}
      </div>
      {props.disabled && <div className="inline-warning">ต้องเชื่อม backend และข้อมูลที่ผ่าน validation ก่อนจึงจะรัน optimizer ได้</div>}
      {props.error && <div className="error-box">{props.error}</div>}
      <Button className="w-full" variant="thermal" disabled={props.loading || props.disabled || !props.interventions.length} onClick={props.onEstimate}>
        {props.loading ? <RefreshCw className="animate-spin" size={15} /> : <Sparkles size={15} />} {props.loading ? "กำลังแก้ปัญหา…" : "คาดการณ์พอร์ตที่เหมาะสม"}
      </Button>
      <p className="readiness-scope">คาดการณ์จากราคาต่อจุดที่อนุมัติแล้วกับทุกจุดคัดกรอง เพื่อวางแผน ไม่ใช่การตัดสินใจลงทุน</p>
      <Button className="mt-2 w-full" variant="outline" disabled={props.loading || props.disabled} onClick={props.onCompare}><FlaskConical size={15} /> เทียบ 3 ยุทธศาสตร์</Button>
      {props.result && (
        <>
          {props.result.mode === "planning_estimate" && (
            <div className="inline-warning"><strong>คาดการณ์พอร์ตเพื่อวางแผนเท่านั้น</strong> จุดเหล่านี้ยังไม่ผ่านการสำรวจพื้นที่ สิทธิ์ในที่ดิน หรือการตรวจแบบ ห้ามใช้ตัดสินใจลงทุน</div>
          )}
          {props.result.mode !== "planning_estimate" && !props.result.allocations.length && (
            <div className="inline-warning">
              {props.result.empty_reason || "ยังไม่มีจุดที่ผ่านหลักฐานรายจุดให้จัดสรรงบจริง"}
              {" "}<button type="button" className="underline" disabled={props.loading} onClick={props.onEstimate}>ดูการคาดการณ์พอร์ตแทน</button>
            </div>
          )}
          <Card className="result-card">
            <div><span>ใช้งบ</span><strong>{formatBaht(props.result.total_cost_thb)}</strong></div>
            <div><span>ผลรวมประมาณการ (ยังไม่หักซ้ำ)</span><strong>{formatNumber(props.result.estimated_beneficiaries)}</strong></div>
            <div><span>โครงการที่เลือก</span><strong>{props.result.allocations.length}</strong></div>
            {props.result.is_outdated && <p className="inline-warning">มีชุดข้อมูลใหม่กว่า กรุณาคำนวณอีกครั้ง</p>}
            {props.result.limitations?.map((item, index) => <p className="readiness-scope" key={index}>{item}</p>)}
          </Card>
          {props.result.mode !== "planning_estimate" && <Button className="mt-2 w-full" variant="outline" disabled={props.exporting} onClick={props.onExport}>
            {props.exporting ? <RefreshCw className="animate-spin" size={15} /> : <Download size={15} />}
            {props.exporting ? "กำลังสร้างรายงาน…" : "ส่งออกรายงานการตัดสินใจ"}
          </Button>}
        </>
      )}
      {!!props.scenarios.length && (
        <>
          <div className="scenario-mini-list">
            {props.scenarios.map((item) => (
              <button className={cn(item.run_id === props.activeScenarioId && "active")} key={item.run_id} onClick={() => props.onScenarioSelect(item.run_id)}>
                <span>{item.strategy}<small>{item.allocations.length} โครงการ · {formatBaht(item.total_cost_thb)}</small></span>
                <strong>{formatNumber(item.estimated_beneficiaries)} คน</strong>
              </button>
            ))}
          </div>
          {props.scenarioMode && (
            <div className="scenario-save-box">
              <label htmlFor="scenario-name">ชื่อฉากทัศน์ที่บันทึก</label>
              <input id="scenario-name" className="text-field" value={props.scenarioName} maxLength={200} onChange={(event) => props.setScenarioName(event.target.value)} />
              <Button className="w-full" variant="outline" disabled={props.savingScenario || !props.scenarioName.trim()} onClick={props.onSaveScenario}>
                {props.savingScenario ? <RefreshCw className="animate-spin" size={14} /> : <Database size={14} />}
                {props.savingScenario ? "กำลังบันทึกเวอร์ชัน…" : "บันทึกผลเปรียบเทียบ"}
              </Button>
              {props.savedScenario && (
                <>
                  <div className={cn("saved-scenario-note", props.savedScenario.is_outdated && "outdated")}>
                    <ShieldCheck size={13} />
                    <span>{props.savedScenario.is_outdated ? "มีข้อมูลใหม่กว่า — ผลเดิมยังถูกเก็บไว้" : "บันทึกแล้ว"}<strong>{props.savedScenario.name}</strong><small>ID {props.savedScenario.id}</small></span>
                  </div>
                  <Button className="w-full" variant="outline" disabled={props.exportingScenario} onClick={props.onExportScenario}>
                    {props.exportingScenario ? <RefreshCw className="animate-spin" size={14} /> : <Download size={14} />}
                    {props.exportingScenario ? "กำลังสร้างรายงาน…" : "ส่งออกรายงาน 3 ยุทธศาสตร์"}
                  </Button>
                </>
              )}
            </div>
          )}
        </>
      )}
      {props.scenarioMode && (
        <div className="scenario-save-box scenario-library-only">
          <div className="saved-scenario-library">
            <span>ฉากทัศน์ที่บันทึกไว้</span>
            {props.savedScenariosLoading && <small><RefreshCw className="animate-spin" size={11} /> กำลังโหลด</small>}
            {!props.savedScenariosLoading && !props.savedScenarioList.length && <small>ยังไม่มีรายการที่บันทึก</small>}
            {props.savedScenarioList.map((item) => (
              <button key={item.id} className={cn(item.id === props.savedScenario?.id && "active", item.is_outdated && "outdated")} onClick={() => props.onOpenSavedScenario(item.id)}>
                <span>{item.name}<small>{formatThaiDate(item.created_at, true)} · {formatBaht(item.budget_thb)}</small></span>
                {props.openingScenarioId === item.id ? <RefreshCw className="animate-spin" size={12} /> : <Badge>{item.is_outdated ? "ข้อมูลใหม่กว่า" : "พร้อมใช้"}</Badge>}
              </button>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

function AssistantPanel({ area, question, setQuestion, answer, optimization, loading, disabled, onAsk, error }: {
  area: AreaProperties | null;
  question: string;
  setQuestion: (value: string) => void;
  answer: string | null;
  optimization: OptimizationResult | null;
  loading: boolean;
  disabled: boolean;
  onAsk: (question?: string) => void;
  error: string | null;
}) {
  const quick = ["ข้อมูลดาวเทียมล่าสุดเมื่อไร?", "ทำไมพื้นที่นี้ได้คะแนนสูง?", "มีงบ 5 ล้านบาท ควรลงทุนตรงไหน?"];
  return (
    <>
      <div className="panel-title"><span>EXPLAINABLE ASSISTANT</span><h2>ถามจากข้อมูลในแผนที่</h2><p>{area ? `บริบทปัจจุบัน: ${area.name_th}` : "เลือกพื้นที่เพื่อเพิ่มบริบทให้คำตอบ"}</p></div>
      <div className="assistant-quick">{quick.map((item) => <button key={item} disabled={disabled} onClick={() => onAsk(item)}>{item}</button>)}</div>
      <textarea className="assistant-input" rows={4} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="ถามเกี่ยวกับข้อมูล พื้นที่ หรือแผนลงทุน…" />
      <Button className="w-full" disabled={disabled || loading || !question.trim()} onClick={() => onAsk()}>{loading ? <RefreshCw className="animate-spin" size={15} /> : <Bot size={15} />} ถาม HEAT//SHIFT</Button>
      {disabled && <div className="inline-warning">ผู้ช่วยจะตอบเมื่อเชื่อม backend เท่านั้น เพื่อไม่สร้างตัวเลขที่ไม่มีแหล่งอ้างอิง</div>}
      {error && <div className="error-box">{error}</div>}
      {answer && <div className="assistant-answer"><span>คำตอบจากข้อมูลระบบ</span><p>{answer}</p></div>}
      {optimization && (
        <Card className="assistant-portfolio">
          <div className="assistant-portfolio-head">
            <span>พอร์ตจาก OR-Tools</span>
            <strong>{formatBaht(optimization.total_cost_thb)}</strong>
          </div>
          <div className="assistant-portfolio-stats">
            <span>{optimization.allocations.length} โครงการ</span>
            <span>ผู้รับประโยชน์ประมาณ {formatNumber(optimization.estimated_beneficiaries)} คน</span>
          </div>
          <div className="assistant-projects">
            {optimization.allocations.slice(0, 5).map((project, index) => (
              <div key={project.id}>
                <i>{String(index + 1).padStart(2, "0")}</i>
                <span>{project.name}<small>{project.intervention_type}</small></span>
                <strong>{formatBaht(project.cost_thb)}</strong>
              </div>
            ))}
          </div>
          <p>ตำแหน่งถูกแสดงบนแผนที่และบันทึก run ไว้ส่งออกรายงานได้ในแท็บ “งบเมือง”</p>
        </Card>
      )}
    </>
  );
}

function HistoryChart({ history, loading, areaName }: { history: HistoryPoint[]; loading: boolean; areaName?: string }) {
  return (
    <div className="history-chart">
      <div className="chart-heading"><div><span>HISTORICAL SIGNAL</span><strong>{areaName || "เลือกพื้นที่เพื่อดูประวัติ"}</strong></div><Badge>OBSERVED</Badge></div>
      <div className="chart-canvas">
        {loading ? <div className="chart-empty"><RefreshCw className="animate-spin" /> กำลังโหลดประวัติ</div> : history.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={history} margin={{ top: 8, right: 10, bottom: 0, left: -20 }}>
              <CartesianGrid stroke="rgba(255,255,255,.06)" vertical={false} />
              <XAxis dataKey="date" tickFormatter={(value) => formatThaiDate(String(value))} minTickGap={24} tick={{ fill: "#78909d", fontSize: 9 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fill: "#78909d", fontSize: 9 }} tickLine={false} axisLine={false} />
              <Tooltip labelFormatter={(value) => formatThaiDate(String(value), true)} contentStyle={{ background: "#0d1922", border: "1px solid rgba(255,255,255,.12)", borderRadius: 8, fontSize: 11 }} />
              <Line type="monotone" dataKey="temperature_c" name="อุณหภูมิพื้นผิว (°C)" stroke="#ff5a36" strokeWidth={2} dot={false} connectNulls />
              <Line type="monotone" dataKey="priority_score" name="คะแนนลำดับความสำคัญ" stroke="#4eeaff" strokeWidth={2} dot={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        ) : <div className="chart-empty"><History /> ยังไม่มีประวัติที่ผ่านการตรวจสอบสำหรับพื้นที่นี้</div>}
      </div>
    </div>
  );
}

const evidenceTypeLabels: Record<string, string> = {
  tree_planting: "ปลูกต้นไม้",
  public_shade_structure: "โครงสร้างบังแดด",
  shaded_walkway: "ทางเดินร่มเงา",
  cooling_rest_area: "จุดพักคลายร้อน",
  reflective_surface: "พื้นผิวสะท้อนความร้อน",
};

function EvidenceApprovalPanel({ operatorToken }: { operatorToken: string }) {
  const [assumptions, setAssumptions] = useState<CostAssumptionRecord[]>([]);
  const [candidates, setCandidates] = useState<CandidateReviewRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [panelError, setPanelError] = useState<string | null>(null);
  const [costType, setCostType] = useState("public_shade_structure");
  const [costVersion, setCostVersion] = useState("");
  const [unitCost, setUnitCost] = useState("");
  const [maintenanceCost, setMaintenanceCost] = useState("");
  const [costEvidenceTitle, setCostEvidenceTitle] = useState("");
  const [costEvidenceUrl, setCostEvidenceUrl] = useState("");
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [selectedAssumptionId, setSelectedAssumptionId] = useState("");
  const [feasibilityUrl, setFeasibilityUrl] = useState("");
  const [feasibilityNotes, setFeasibilityNotes] = useState("");
  const [siteChecked, setSiteChecked] = useState(false);
  const [evidenceMethod, setEvidenceMethod] = useState<"desk_review" | "field_survey">("desk_review");
  const [landChecked, setLandChecked] = useState(false);
  const [accessChecked, setAccessChecked] = useState(false);

  const reloadEvidence = useCallback(async () => {
    setLoading(true);
    try {
      const [costRows, candidateRows] = await Promise.all([
        loadCostAssumptions(),
        loadEvidencePendingCandidates(),
      ]);
      setAssumptions(costRows);
      setCandidates(candidateRows);
      setSelectedCandidateId((current) => current && candidateRows.some((item) => item.id === current)
        ? current
        : candidateRows[0]?.id || "");
    } catch (loadError) {
      setPanelError(loadError instanceof Error ? loadError.message : "โหลดข้อมูลหลักฐานไม่สำเร็จ");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void reloadEvidence(); }, [reloadEvidence]);

  const selectedCandidate = candidates.find((item) => item.id === selectedCandidateId) || null;
  const matchingAssumptions = assumptions.filter(
    (item) => item.intervention_type === selectedCandidate?.intervention_type,
  );

  useEffect(() => {
    setSelectedAssumptionId((current) => matchingAssumptions.some((item) => item.id === current)
      ? current
      : matchingAssumptions[0]?.id || "");
  }, [selectedCandidateId, assumptions]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submitCost(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPanelError(null);
    setMessage(null);
    const parsedCost = Number(unitCost);
    const parsedMaintenance = maintenanceCost ? Number(maintenanceCost) : null;
    if (!operatorToken) {
      setPanelError("กรอกรหัสผู้ดูแลด้านบนก่อนบันทึกหลักฐาน");
      return;
    }
    if (!Number.isFinite(parsedCost) || parsedCost <= 0 || (parsedMaintenance != null && (!Number.isFinite(parsedMaintenance) || parsedMaintenance < 0))) {
      setPanelError("กรอกต้นทุนเป็นตัวเลขที่มากกว่าศูนย์ และค่าบำรุงรักษาต้องไม่ติดลบ");
      return;
    }
    setSaving(true);
    try {
      const created = await createCostAssumption({
        intervention_type: costType,
        version: costVersion.trim(),
        unit_cost_thb: parsedCost,
        annual_maintenance_thb: parsedMaintenance,
        evidence_url: costEvidenceUrl.trim(),
        evidence_title: costEvidenceTitle.trim(),
      }, operatorToken);
      setAssumptions((current) => [created, ...current]);
      setCostVersion("");
      setUnitCost("");
      setMaintenanceCost("");
      setCostEvidenceTitle("");
      setCostEvidenceUrl("");
      setMessage("บันทึกสมมติฐานต้นทุนแบบ versioned พร้อมหลักฐานแล้ว");
    } catch (saveError) {
      setPanelError(saveError instanceof Error ? saveError.message : "บันทึกสมมติฐานไม่สำเร็จ");
    } finally {
      setSaving(false);
    }
  }

  async function submitApproval(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPanelError(null);
    setMessage(null);
    if (!operatorToken) {
      setPanelError("กรอกรหัสผู้ดูแลด้านบนก่อนอนุมัติ candidate");
      return;
    }
    if (!selectedCandidate || !selectedAssumptionId) {
      setPanelError("เลือก candidate และสมมติฐานต้นทุนประเภทเดียวกันก่อน");
      return;
    }
    if (!siteChecked || !landChecked || !accessChecked) {
      setPanelError("ต้องยืนยันผลสำรวจ สิทธิใช้พื้นที่ และการออกแบบ/การเข้าถึงครบทุกข้อ");
      return;
    }
    setSaving(true);
    try {
      await approveCandidate(selectedCandidate.id, {
        cost_assumption_id: selectedAssumptionId,
        evidence_method: evidenceMethod,
        feasibility_evidence_url: feasibilityUrl.trim(),
        feasibility_notes: feasibilityNotes.trim(),
        confirm_site_feasibility: siteChecked,
        confirm_land_control: landChecked,
        confirm_design_accessibility_review: accessChecked,
      }, operatorToken);
      const remaining = candidates.filter((item) => item.id !== selectedCandidate.id);
      setCandidates(remaining);
      setSelectedCandidateId(remaining[0]?.id || "");
      setFeasibilityUrl("");
      setFeasibilityNotes("");
      setSiteChecked(false);
      setLandChecked(false);
      setAccessChecked(false);
      setMessage("อนุมัติ candidate แล้ว ระบบทำเครื่องหมายแผน optimization เดิมว่าล้าสมัย");
    } catch (approvalError) {
      setPanelError(approvalError instanceof Error ? approvalError.message : "อนุมัติ candidate ไม่สำเร็จ");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="evidence-approval-panel">
      <div className="evidence-panel-heading">
        <div><span>EVIDENCE GATE</span><strong>หลักฐานก่อนนำจุดเข้า optimizer</strong></div>
        <button onClick={() => void reloadEvidence()} disabled={loading} aria-label="โหลดข้อมูลหลักฐานใหม่"><RefreshCw className={cn(loading && "animate-spin")} size={13} /></button>
      </div>
      <p className="evidence-warning">ตำแหน่ง OSM เป็นเพียงจุดคัดกรอง ระบบจะไม่สร้างต้นทุนหรือผลประโยชน์ขึ้นเอง และไม่ถือว่าอนุมัติจนกว่าจะมีหลักฐานครบ</p>
      <div className="evidence-grid">
        <form className="evidence-form" onSubmit={submitCost}>
          <h3>1. เพิ่มสมมติฐานต้นทุน</h3>
          <label><span>ประเภทโครงการ</span><select value={costType} onChange={(event) => setCostType(event.target.value)}>{Object.entries(evidenceTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <div className="evidence-form-row">
            <label><span>เวอร์ชัน</span><input required maxLength={80} value={costVersion} onChange={(event) => setCostVersion(event.target.value)} placeholder="เช่น bma-2026.1" /></label>
            <label><span>ต้นทุนต่อจุด (บาท)</span><input required type="number" min="1" value={unitCost} onChange={(event) => setUnitCost(event.target.value)} /></label>
          </div>
          <label><span>ค่าบำรุงรักษาต่อปี (ถ้ามี)</span><input type="number" min="0" value={maintenanceCost} onChange={(event) => setMaintenanceCost(event.target.value)} /></label>
          <label><span>ชื่อเอกสารหลักฐาน</span><input required minLength={3} value={costEvidenceTitle} onChange={(event) => setCostEvidenceTitle(event.target.value)} /></label>
          <label><span>URL หลักฐาน</span><input required type="url" value={costEvidenceUrl} onChange={(event) => setCostEvidenceUrl(event.target.value)} placeholder="https://..." /></label>
          <Button type="submit" disabled={saving || !operatorToken}><ShieldCheck size={13} /> บันทึกสมมติฐาน</Button>
        </form>
        <form className="evidence-form" onSubmit={submitApproval}>
          <h3>2. อนุมัติ candidate</h3>
          <label><span>จุดที่รอหลักฐาน ({candidates.length})</span><select required value={selectedCandidateId} onChange={(event) => setSelectedCandidateId(event.target.value)}><option value="">ยังไม่มี candidate</option>{candidates.map((item) => <option key={item.id} value={item.id}>{item.name_th} · {evidenceTypeLabels[item.intervention_type] || item.intervention_type}</option>)}</select></label>
          {selectedCandidate && <div className="candidate-evidence-meta"><span>Priority {formatNumber(selectedCandidate.priority_score, 1)}</span><span>{selectedCandidate.area_id}</span></div>}
          <label><span>สมมติฐานต้นทุนที่ตรงประเภท</span><select required value={selectedAssumptionId} onChange={(event) => setSelectedAssumptionId(event.target.value)}><option value="">ยังไม่มีสมมติฐานที่ใช้ได้</option>{matchingAssumptions.map((item) => <option key={item.id} value={item.id}>{item.version} · {formatBaht(item.assumptions.unit_cost_thb || 0)}</option>)}</select></label>
          <label><span>วิธีตรวจหลักฐาน</span><select value={evidenceMethod} onChange={(event) => setEvidenceMethod(event.target.value as "desk_review" | "field_survey")}>
            <option value="desk_review">ตรวจจากระยะไกล (ภาพดาวเทียม แผนที่ ข้อมูลที่ดินสาธารณะ)</option>
            <option value="field_survey">ลงพื้นที่สำรวจจริง</option>
          </select></label>
          {evidenceMethod === "desk_review" && <p className="readiness-scope">ใช้วางแผนและจัดลำดับงบได้ แต่ต้องยืนยันหน้างานก่อนเริ่มก่อสร้าง</p>}
          <label><span>{evidenceMethod === "desk_review" ? "URL หลักฐานออนไลน์ (แผนที่/ภาพดาวเทียม/ประกาศที่ดิน)" : "URL รายงานตรวจพื้นที่"}</span><input required type="url" value={feasibilityUrl} onChange={(event) => setFeasibilityUrl(event.target.value)} placeholder="https://..." /></label>
          {evidenceMethod === "desk_review" && selectedCandidate && osmCandidateUrl(selectedCandidate.id) && (
            <button type="button" className="underline text-left" onClick={() => setFeasibilityUrl(osmCandidateUrl(selectedCandidate.id) || "")}>ใช้ลิงก์ OpenStreetMap ของจุดนี้</button>
          )}
          <label><span>บันทึกข้อจำกัดและผลตรวจ</span><textarea required minLength={10} value={feasibilityNotes} onChange={(event) => setFeasibilityNotes(event.target.value)} /></label>
          <div className="evidence-checks">
            <label><input type="checkbox" checked={siteChecked} onChange={(event) => setSiteChecked(event.target.checked)} /> {evidenceMethod === "desk_review" ? "ดูภาพดาวเทียมแล้ว มีพื้นที่ว่างพอ" : "สำรวจหน้างานแล้ว"}</label>
            <label><input type="checkbox" checked={landChecked} onChange={(event) => setLandChecked(event.target.checked)} /> {evidenceMethod === "desk_review" ? "ข้อมูลสาธารณะระบุว่าเป็นที่ดินของรัฐ/กทม." : "ตรวจสิทธิใช้พื้นที่แล้ว"}</label>
            <label><input type="checkbox" checked={accessChecked} onChange={(event) => setAccessChecked(event.target.checked)} /> {evidenceMethod === "desk_review" ? "แผนที่แสดงทางเข้าจากทางสาธารณะ" : "ทบทวนการออกแบบและการเข้าถึงแล้ว"}</label>
          </div>
          <Button type="submit" disabled={saving || !operatorToken || !selectedCandidate}><ShieldCheck size={13} /> อนุมัติเข้า optimizer</Button>
        </form>
      </div>
      {message && <div className="evidence-message success">{message}</div>}
      {panelError && <div className="evidence-message error">{panelError}</div>}
    </section>
  );
}

function ObservatoryOverlay({ datasets, snapshot, readiness, readinessError, loading, onRefresh, refreshing, job, error }: {
  datasets: DatasetStatus[];
  snapshot: ObservatorySnapshot | null;
  readiness: ProductionReadiness | null;
  readinessError: string | null;
  loading: boolean;
  onRefresh: (source: string, operatorToken: string) => void;
  refreshing: boolean;
  job: { job_id: string; status: string } | null;
  error: string | null;
}) {
  const [source, setSource] = useState("open-meteo");
  const [operatorToken, setOperatorToken] = useState("");
  const [adminSession, setAdminSession] = useState<AdminSessionStatus | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const credential = adminSession?.authenticated ? "__session__" : operatorToken;
  useEffect(() => {
    let cancelled = false;
    const refresh = () => void loadAdminSession().then((session) => {
      if (!cancelled) { setAdminSession(session); setSessionError(null); }
    }).catch(() => { if (!cancelled) setSessionError("ตรวจสิทธิ์ผู้ดูแลไม่สำเร็จ"); });
    refresh();
    const timer = setInterval(refresh, 30_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);
  const [boundaryCheck, setBoundaryCheck] = useState<string | null>(null);
  useEffect(() => {
    // The official catalogs block the hosting region, so an administrator's
    // browser in Thailand confirms the district boundary version once per visit.
    if (!adminSession?.authenticated) return;
    let cancelled = false;
    confirmBoundariesFromBrowser("__session__")
      .then((result) => { if (!cancelled) setBoundaryCheck(result.current_version_confirmed
        ? "ยืนยันขอบเขต 50 เขตกับ data.go.th จากเบราว์เซอร์นี้แล้ว"
        : "data.go.th มีขอบเขตเวอร์ชันใหม่กว่า ต้องทบทวนก่อนเผยแพร่"); })
      .catch((checkError) => { if (!cancelled) setBoundaryCheck(checkError instanceof Error ? checkError.message : "ตรวจขอบเขตจากเบราว์เซอร์ไม่สำเร็จ"); });
    return () => { cancelled = true; };
  }, [adminSession?.authenticated]);
  const signOut = async () => {
    try { await logoutAdmin(); setAdminSession((value) => value ? { ...value, authenticated: false, actor: undefined, csrf_token: undefined } : null); }
    catch { setSessionError("ออกจากระบบไม่สำเร็จ กรุณาลองอีกครั้ง"); }
  };
  const [versionHistory, setVersionHistory] = useState<DatasetVersionHistory | null>(null);
  const [versionHistoryLoading, setVersionHistoryLoading] = useState(false);
  const [versionHistoryError, setVersionHistoryError] = useState<string | null>(null);
  const inspectVersions = async (sourceId: string) => {
    setVersionHistoryLoading(true);
    setVersionHistoryError(null);
    try {
      setVersionHistory(await loadDatasetVersions(sourceId));
    } catch (loadError) {
      setVersionHistoryError(loadError instanceof Error ? loadError.message : "โหลดประวัติเวอร์ชันไม่สำเร็จ");
    } finally {
      setVersionHistoryLoading(false);
    }
  };
  const queue = {
    queued: snapshot?.queue?.queued ?? 0,
    running: snapshot?.queue?.running ?? 0,
    completed: snapshot?.queue?.completed ?? 0,
    failed: snapshot?.queue?.failed ?? 0,
  };
  const summary = snapshot?.summary || {
    discovered_assets_latest_checks: 0,
    processed_assets_latest_checks: 0,
    failed_jobs_total: 0,
    sources_with_errors: 0,
  };
  const coverage = snapshot?.coverage || { geographic_areas: 0, areas_with_indicators: 0, percent: 0 };
  const sourceChecks = snapshot?.source_checks || [];
  const recentJobs = snapshot?.recent_jobs || [];
  const recentEvents = snapshot?.recent_events || [];
  return (
    <motion.div className="map-overlay-panel observatory" initial={false} animate={{ opacity: 1, scale: 1 }}>
      <div className="overlay-heading"><div><Badge>PIPELINE MONITOR</Badge><h2>สุขภาพระบบข้อมูล</h2><p>เวลาสังเกตการณ์และเวลาที่ระบบตรวจสอบถูกแสดงแยกกันเสมอ</p></div></div>
      <section className={cn("readiness-panel", readiness?.configuration_ready ? "ready" : "blocked")}>
        <div className="readiness-heading">
          <div><span>ตรวจความพร้อมระบบ</span><strong>{readiness?.configuration_ready ? "ผ่านการตรวจเบื้องต้น" : readiness ? "ยังมีรายการต้องจัดการ" : readinessError ? "ยังตรวจสอบไม่ได้" : "กำลังตรวจสอบ"}</strong><small>{readiness ? `ตรวจ ${formatThaiDate(readiness.generated_at, true)} · โหมด ${readiness.environment}` : "ตรวจการตั้งค่าและการเชื่อมต่อบริการ"}</small></div>
          {readiness && <Badge>{readiness.summary.ready} พร้อม · {readiness.summary.warning} เตือน · {readiness.summary.blocked} ติดขัด</Badge>}
        </div>
        {readinessError && <p className="readiness-error" role="status">{readinessError}{readiness ? " · ด้านล่างเป็นผลตรวจครั้งก่อน" : ""}</p>}
        {readiness ? (
          <details className="readiness-details">
            <summary>ดูผลตรวจ {readiness.checks.length} รายการและวิธีแก้ไข</summary>
            <div className="readiness-check-grid">
              {readiness.checks.map((check) => (
                <article key={check.code} className={check.status}>
                  <span className="readiness-status-dot" role="img" aria-label={check.status === "ready" ? "ผ่าน" : check.status === "warning" ? "ควรตรวจเพิ่ม" : "ต้องแก้ไข"} />
                  <span><strong>{check.label_th}</strong><small>{check.detail_th}</small>{check.action_th && <em>{check.action_th}</em>}</span>
                </article>
              ))}
            </div>
            <p className="readiness-scope">{readiness.limitations_th}</p>
          </details>
        ) : !readinessError && <div className="observatory-loading"><RefreshCw className="animate-spin" size={13} /> กำลังตรวจความพร้อมระบบ</div>}
        <p className="readiness-privacy"><ShieldCheck size={12} /> ผลตรวจแสดงเฉพาะสถานะการตั้งค่าและการเชื่อมต่อ</p>
      </section>
      <DataReadinessCard />
      <div className="pipeline-summary-grid">
        <Card><span>กำลังรอ / ทำงาน</span><strong>{queue.queued} / {queue.running}</strong><small>durable job queue</small></Card>
        <Card><span>Asset ที่พบ / ประมวลผล</span><strong>{summary.discovered_assets_latest_checks} / {summary.processed_assets_latest_checks}</strong><small>จาก source check ล่าสุด</small></Card>
        <Card className={summary.failed_jobs_total ? "has-error" : ""}><span>งานล้มเหลว</span><strong>{summary.failed_jobs_total}</strong><small>เก็บเหตุผลไว้ตรวจสอบ</small></Card>
        <Card><span>พื้นที่ที่มี indicator</span><strong>{formatNumber(coverage.percent, 1)}%</strong><small>{coverage.areas_with_indicators} / {coverage.geographic_areas} พื้นที่</small></Card>
      </div>
      <div className="refresh-controls">
        <label><span>แหล่งข้อมูล</span><select value={source} onChange={(event) => setSource(event.target.value)}><option value="bma-boundaries">ขอบเขต 50 เขต กทม.</option><option value="open-meteo">Open-Meteo</option><option value="landsat">Landsat 8/9</option><option value="sentinel">Sentinel-2</option><option value="dynamic-world">Dynamic World</option><option value="worldpop">WorldPop</option><option value="osm">OpenStreetMap</option></select></label>
        <div className="admin-login-control">
          {adminSession?.authenticated ? <><span>ผู้ดูแล: {adminSession.actor}</span><button onClick={() => void signOut()}>ออกจาก HEAT//SHIFT</button></>
            : adminSession?.mode === "local_key" ? <label><span>สิทธิ์ผู้ดูแลสำหรับพัฒนาในเครื่อง</span><input type="password" autoComplete="current-password" value={operatorToken} onChange={(event) => setOperatorToken(event.target.value)} placeholder="Local operator token" /></label>
              : adminSession?.configured ? <a href="/api/auth/login">เข้าสู่ระบบผู้ดูแลด้วย SSO</a> : <span>{sessionError || "ยังไม่ได้ตั้งค่า SSO สำหรับผู้ดูแล"}</span>}
        </div>
        <Button onClick={() => onRefresh(source, credential)} disabled={refreshing || !credential} variant="outline">{refreshing ? <RefreshCw className="animate-spin" size={14} /> : <RefreshCw size={14} />} ตรวจสอบตอนนี้</Button>
      </div>
      {boundaryCheck && <p className="readiness-scope">{boundaryCheck}</p>}
      {sessionError && <div className="error-box">{sessionError}</div>}
      <EvidenceApprovalPanel operatorToken={credential} />
      <HistoricalIngestionPanel credential={credential} />
      {error && <div className="error-box">{error}</div>}
      {job && <div className="job-box"><Activity size={14} /><span>Job {job.job_id}</span><strong>{job.status}</strong></div>}
      {loading && <div className="observatory-loading"><RefreshCw className="animate-spin" size={14} /> กำลังโหลดสถานะ pipeline</div>}
      <div className="dataset-grid">
        {datasets.map((item) => (
          <Card key={item.dataset_id} className="dataset-card">
            <div className="dataset-title"><strong>{item.source_name}</strong><StatusDot status={item.status} /></div>
            <dl>
              <div><dt>{item.dataset_id === "worldpop/global" ? "ปีอ้างอิงประชากร" : "สังเกตการณ์ต้นทาง"}</dt><dd>{item.dataset_id === "worldpop/global" ? formatPopulationReferenceYear(item.last_source_observed_at) : formatThaiDate(item.last_source_observed_at, true)}</dd></div>
              <div><dt>ระบบตรวจล่าสุด</dt><dd>{formatThaiDate(item.last_checked_at, true)}</dd></div>
              <div><dt>เผยแพร่ล่าสุด</dt><dd>{formatThaiDate(item.last_published_at, true)}</dd></div>
              <div><dt>ตรวจครั้งถัดไป</dt><dd>{formatThaiDate(item.next_check_at, true)}</dd></div>
            </dl>
            <p>{item.status_reason || `คุณภาพ: ${item.quality_status || "ยังไม่ระบุ"}`}</p>
            {item.dataset_id === "worldpop/global" && <p>ค่าประมาณประชากรจากแบบจำลอง ไม่ใช่การตรวจวัดรายวัน</p>}
            {item.source_id && <button className="version-history-button" onClick={() => void inspectVersions(item.source_id!)}><History size={12} /> ตรวจ version และ validation</button>}
          </Card>
        ))}
      </div>
      {(versionHistoryLoading || versionHistory || versionHistoryError) && (
        <div className="version-history-panel">
          <div className="version-history-head">
            <div><span>DATA PROVENANCE</span><strong>{versionHistory?.source.source_name || "กำลังโหลดประวัติ"}</strong><small>{versionHistory?.source.dataset_id}</small></div>
            <div>
              {versionHistory?.source.id && <button onClick={() => void inspectVersions(versionHistory.source.id)} aria-label="รีโหลดประวัติเวอร์ชัน"><RefreshCw className={cn(versionHistoryLoading && "animate-spin")} size={13} /></button>}
              <button onClick={() => { setVersionHistory(null); setVersionHistoryError(null); }} aria-label="ปิดประวัติเวอร์ชัน"><X size={14} /></button>
            </div>
          </div>
          {versionHistoryLoading && !versionHistory && <div className="observatory-loading"><RefreshCw className="animate-spin" size={13} /> กำลังอ่าน validation reports</div>}
          {versionHistoryError && <div className="error-box">{versionHistoryError}</div>}
          {versionHistory && (
            <div className="version-history-list">
              {versionHistory.items.map((version) => (
                <article key={version.id} className={cn(version.status === "failed" && "failed")}>
                  <div className="version-row-head">
                    <span><strong>{version.source_asset_id}</strong><small>{version.source_version}</small></span>
                    <Badge>{version.status}</Badge>
                  </div>
                  <dl>
                    <div><dt>{versionHistory.source.dataset_id === "worldpop/global" ? "ปีอ้างอิงประชากร" : "สังเกตการณ์ต้นทาง"}</dt><dd>{versionHistory.source.dataset_id === "worldpop/global" ? formatPopulationReferenceYear(version.observed_at) : formatThaiDate(version.observed_at, true)}</dd></div>
                    <div><dt>เผยแพร่</dt><dd>{formatThaiDate(version.published_at, true)}</dd></div>
                    <div><dt>Layer version</dt><dd>v{version.layer_version}</dd></div>
                    <div><dt>คุณภาพ</dt><dd>{version.quality_status}</dd></div>
                  </dl>
                  {version.validation ? (
                    <div className={cn("validation-summary", version.validation.passed ? "passed" : "rejected")}>
                      <span>{version.validation.passed ? "VALIDATION PASSED" : "VALIDATION REJECTED"}</span>
                      <small>{Object.entries(version.validation.checks).map(([key, value]) => `${key}: ${String(value)}`).join(" · ") || "ไม่มี metric เพิ่มเติม"}</small>
                      {!!version.validation.warnings.length && <p>คำเตือน: {version.validation.warnings.join(" · ")}</p>}
                      {!!version.validation.errors.length && <p>เหตุผล: {version.validation.errors.join(" · ")}</p>}
                    </div>
                  ) : <div className="validation-summary"><span>ยังไม่มี validation report</span></div>}
                  <div className="version-foot"><span>Payload เก็บในระบบ: {version.provider_payload_preserved ? "ใช่ (ไม่เปิดเผย raw ผ่าน API)" : "ไม่"}</span><span>{version.checksum ? `SHA-256 ${version.checksum.slice(0, 12)}…` : "ไม่มี checksum"}</span></div>
                </article>
              ))}
              {!versionHistory.items.length && <p className="timeline-empty">ยังไม่มี dataset version สำหรับแหล่งนี้</p>}
              <div className="version-history-count">แสดง {versionHistory.items.length} จาก {versionHistory.total} เวอร์ชัน</div>
            </div>
          )}
        </div>
      )}
      {snapshot && (
        <div className="observatory-details">
          <section>
            <div className="section-label"><span>Source checks และ quota</span><Database size={13} /></div>
            <div className="source-check-table">
              {sourceChecks.map((check) => (
                <div key={check.source_id}>
                  <span><strong>{check.source_name}</strong><small>{formatThaiDate(check.checked_at, true)}</small></span>
                  <span>พบ {check.discovered_count} · ส่งประมวลผล {check.processed_count}</span>
                  <span className={cn(check.status === "failed" && "text-red-300")}>{check.status}</span>
                  <span>{check.provider_quota.status === "not_reported" ? "quota: ยังไม่รายงาน" : `quota: ${String(check.provider_quota.status || "reported")}`}</span>
                </div>
              ))}
              {!sourceChecks.length && <p className="timeline-empty">backend รุ่นนี้ยังไม่ส่งรายละเอียด source check</p>}
            </div>
          </section>
          <section>
            <div className="section-label"><span>Ingestion activity</span><History size={13} /></div>
            <div className="activity-timeline">
              {recentJobs.map((item) => (
                <div key={item.id} className={cn("activity-item", item.status === "failed" && "failed")}>
                  <StatusDot status={item.status === "failed" ? "error" : item.status === "completed" ? "up_to_date" : "updating"} label={false} />
                  <span><strong>{item.job_type} · {item.stage}</strong><small>{formatThaiDate(item.created_at, true)} · attempt {item.attempts}</small></span>
                  <span>{formatNumber(item.progress * 100)}%</span>
                </div>
              ))}
              {!recentJobs.length && <p className="timeline-empty">ยังไม่มี ingestion job</p>}
            </div>
          </section>
          <section>
            <div className="section-label"><span>Publication events</span><Activity size={13} /></div>
            <div className="event-list">
              {recentEvents.map((event) => (
                <div key={event.id}><span>{event.type}</span><strong>{event.layer || "system"} {event.layer_version != null ? `v${event.layer_version}` : ""}</strong><time>{formatThaiDate(event.created_at, true)}</time></div>
              ))}
              {!recentEvents.length && <p className="timeline-empty">ยังไม่มี publication event</p>}
            </div>
          </section>
        </div>
      )}
    </motion.div>
  );
}

function MethodologyOverlay() {
  const cards = [
    ["01", "ข้อมูลมาก่อนคะแนน", "ระบบเผยแพร่เฉพาะ observation ที่ผ่าน validation และเก็บเวอร์ชันเดิมไว้เพื่อทำซ้ำผลลัพธ์ได้"],
    ["02", "คะแนนอธิบายได้", "Heat Exposure Priority รวม temperature anomaly, population exposure, vegetation deficit และ built-up intensity โดยเปิดเผยน้ำหนัก"],
    ["03", "ไม่สรุปความเหลื่อมล้ำเกินหลักฐาน", "คะแนนสูงหมายถึงความสำคัญด้านการสัมผัสความร้อน ไม่ได้ใช้แทนรายได้หรือฐานะทางเศรษฐกิจ"],
    ["04", "อนาคตคือฉากทัศน์", "ผล +1, +3 และ +5 ปีต้องระบุ model version, training window, spatial holdout และ uncertainty interval"],
  ];
  return (
    <motion.div className="map-overlay-panel methodology" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
      <Badge>TRACEABLE BY DESIGN</Badge><h2>จากข้อมูลดิบสู่การตัดสินใจ</h2><p className="method-intro">ทุกคำแนะนำต้องย้อนกลับไปยังชุดข้อมูล เวอร์ชัน สูตร และข้อสมมติฐานที่ใช้ได้</p>
      <div className="method-grid">{cards.map(([id, title, body]) => <Card key={id}><span>{id}</span><h3>{title}</h3><p>{body}</p></Card>)}</div>
      <div className="formula-box"><span>HEAT EXPOSURE PRIORITY</span><code>100 × weighted_mean(valid normalized indicators)</code><p>ตัวหารปรับตามตัวแปรที่มีจริง และบันทึก missing-data coverage ทุกครั้ง</p></div>
    </motion.div>
  );
}
