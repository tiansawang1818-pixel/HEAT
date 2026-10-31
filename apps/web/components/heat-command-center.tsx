"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Activity,
  BarChart3,
  Bot,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  Database,
  Download,
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
  compareScenarios,
  downloadOptimizationReport,
  loadAreaDetail,
  loadAreaHistory,
  loadDashboardData,
  loadIngestionStatus,
  loadLayerFeatures,
  loadObservatoryStatus,
  loadOptimizationRun,
  runOptimization,
  streamUrl,
  triggerRefresh,
} from "@/lib/api";
import { defaultLayers, demoDatasets, demoFeatures, emptyHistory } from "@/lib/demo-data";
import {
  cn,
  formatBaht,
  formatNumber,
  formatThaiDate,
  freshnessLabel,
  type AreaProperties,
  type DatasetStatus,
  type FeatureCollection,
  type GeoFeature,
  type HistoryPoint,
  type MapLayerMeta,
  type ObservatorySnapshot,
  type OptimizationResult,
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
};

const interventionOptions = [
  { id: "tree_planting", label: "ปลูกต้นไม้" },
  { id: "shade_structure", label: "โครงสร้างบังแดด" },
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
  isDemo: boolean;
}

const initialState: DashboardState = {
  datasets: demoDatasets,
  layers: defaultLayers,
  features: demoFeatures,
  isDemo: true,
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
  const [activeLayer, setActiveLayer] = useState("priority");
  const [selected, setSelected] = useState<GeoFeature | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>(emptyHistory);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [query, setQuery] = useState("");
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
  const [refreshJob, setRefreshJob] = useState<{ job_id: string; status: string } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [assistantQuestion, setAssistantQuestion] = useState("");
  const [assistantAnswer, setAssistantAnswer] = useState<string | null>(null);
  const [assistantLoading, setAssistantLoading] = useState(false);
  const [observatory, setObservatory] = useState<ObservatorySnapshot | null>(null);
  const [observatoryLoading, setObservatoryLoading] = useState(initialView === "observatory");

  const reload = useCallback(async (notify = false) => {
    try {
      const next = await loadDashboardData();
      setData(next);
      if (notify) setUpdateNotice("เผยแพร่ข้อมูลเวอร์ชันใหม่แล้ว — แผนที่อัปเดตโดยไม่เปลี่ยนมุมมอง");
    } finally {
      setLoading(false);
    }
  }, []);

  const reloadOptimizationStatus = useCallback(async () => {
    if (!optimization?.run_id) return;
    try {
      const latest = await loadOptimizationRun(optimization.run_id);
      setOptimization({ ...latest, strategy: optimization.strategy });
    } catch {
      // Keep the historical result visible if status synchronization is temporarily unavailable.
    }
  }, [optimization?.run_id, optimization?.strategy]);

  useEffect(() => {
    void reload();
    let eventSource: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    try {
      eventSource = new EventSource(streamUrl());
      eventSource.onopen = () => setConnection("live");
      eventSource.addEventListener("data-update", () => {
        void reload(true);
        void reloadOptimizationStatus();
      });
      eventSource.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as { type?: string };
          if (payload.type === "data-update" || payload.type === "dataset_published") {
            void reload(true);
            void reloadOptimizationStatus();
          }
        } catch {
          // Heartbeat messages may be plain text.
        }
      };
      eventSource.onerror = () => {
        eventSource?.close();
        setConnection("polling");
      };
    } catch {
      setConnection("polling");
    }
    pollTimer = setInterval(() => {
      if (!eventSource || eventSource.readyState === EventSource.CLOSED) {
        void reload();
        void reloadOptimizationStatus();
      }
    }, 60_000);
    return () => {
      eventSource?.close();
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [reload, reloadOptimizationStatus]);

  useEffect(() => {
    if (data.isDemo) return;
    let cancelled = false;
    loadLayerFeatures(activeLayer)
      .then((features) => {
        if (!cancelled) setData((current) => ({ ...current, features }));
      })
      .catch((error) => setActionError(error instanceof Error ? error.message : "โหลดชั้นข้อมูลไม่สำเร็จ"));
    return () => { cancelled = true; };
  }, [activeLayer, data.isDemo]);

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
          if (job.status === "completed") void reload(true);
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
    };
    refresh();
    const timer = setInterval(refresh, 10_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [initialView]);

  const handleSelect = useCallback((feature: GeoFeature) => {
    setSelected(feature);
    setRightTab("area");
    setMobilePanel("analysis");
    setHistoryLoading(true);
    loadAreaDetail(feature.properties.id)
      .then((detail) => setSelected((current) => current ? { ...current, properties: { ...current.properties, ...detail } } : current))
      .catch(() => undefined);
    loadAreaHistory(feature.properties.id)
      .then(setHistory)
      .catch(() => setHistory([]))
      .finally(() => setHistoryLoading(false));
  }, []);

  const visibleFeatures = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("th-TH");
    if (!normalized) return data.features.features;
    return data.features.features.filter((feature) =>
      `${feature.properties.name_th} ${feature.properties.name_en || ""}`.toLocaleLowerCase("th-TH").includes(normalized),
    );
  }, [data.features.features, query]);

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

  async function handleOptimize(compare = false) {
    setOptimizing(true);
    setActionError(null);
    try {
      if (compare) {
        const results = await compareScenarios({ budget_thb: budget, intervention_types: interventions });
        setScenarioResults(results);
        setActiveScenarioId(results.at(-1)?.run_id || null);
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
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "ผู้ช่วยยังไม่พร้อมใช้งาน");
    } finally {
      setAssistantLoading(false);
    }
  }

  async function handleExportReport() {
    if (!optimization?.run_id) return;
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

  const currentLayer = data.layers.find((layer) => layer.id === activeLayer) || defaultLayers.find((layer) => layer.id === activeLayer);
  const activeScenario = scenarioResults.find((item) => item.run_id === activeScenarioId) || scenarioResults.at(-1);
  const displayedProjects = initialView === "scenarios" && activeScenario ? activeScenario.allocations : optimization?.allocations;

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

      {data.isDemo && (
        <div className="demo-banner" role="status">
          <ShieldCheck size={14} />
          <strong>โหมดสาธิตเฉพาะโครง UX</strong>
          <span>backend หรือชุดข้อมูลจริงยังไม่พร้อม — รูปร่างบนแผนที่เป็นพื้นที่ตัวอย่างและไม่มีค่าการวัดสมมติ</span>
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
              {visibleFeatures.slice(0, 5).map((feature) => (
                <button key={feature.properties.id} onClick={() => handleSelect(feature)}>{feature.properties.name_th}</button>
              ))}
              {!visibleFeatures.length && <p>ไม่พบพื้นที่</p>}
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
          <div className="section-label"><span>วันที่สังเกตการณ์</span><Clock3 size={13} /></div>
          <button className="date-select">{formatThaiDate(currentLayer?.observation_date)}<ChevronDown size={13} /></button>
          <p>แสดงวันที่ต้นทาง ไม่ใช่วันที่นำเข้าระบบ</p>
        </div>
      </aside>

      <section className="map-stage">
        <HeatMap
          data={data.features}
          activeLayer={activeLayer}
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
        <div className="map-title-card">
          <span>{viewCopy[initialView].eyebrow}</span>
          <h1>{viewCopy[initialView].title}</h1>
          <p>ข้อมูลแต่ละชั้นเผยแพร่เมื่อผ่าน validation แล้วเท่านั้น</p>
        </div>
        <div className="legend-card">
          <div className="legend-heading"><span>{currentLayer?.name_th || activeLayer}</span><Badge>{currentLayer?.unit || "INDEX"}</Badge></div>
          <div className={cn("legend-ramp", (activeLayer === "vegetation" || activeLayer === "ndvi") && "green")} />
          <div className="legend-labels"><span>ต่ำ</span><span>สูง</span></div>
          <div className="legend-meta"><StatusDot status={currentLayer?.status || "unavailable"} /><span>v{currentLayer?.layer_version ?? "—"}</span></div>
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
              <AreaPanel area={selected?.properties || null} loading={loading} onOptimize={() => setRightTab("optimizer")} />
            </motion.div>
          )}
          {rightTab === "optimizer" && (
            <motion.div key="optimizer" className="panel-scroll" initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}>
              <OptimizerPanel
                budget={budget}
                setBudget={setBudget}
                strategy={strategy}
                setStrategy={setStrategy}
                interventions={interventions}
                setInterventions={setInterventions}
                onRun={() => void handleOptimize(false)}
                onCompare={() => void handleOptimize(true)}
                onExport={() => void handleExportReport()}
                loading={optimizing}
                exporting={exportingReport}
                result={optimization}
                scenarios={scenarioResults}
                activeScenarioId={activeScenario?.run_id || null}
                onScenarioSelect={setActiveScenarioId}
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
            <span>ผู้รับประโยชน์ <strong>{formatNumber(scenario.estimated_beneficiaries)}</strong></span>
          </div>
        </article>
      ))}
      <div className="scenario-sync-note"><Activity size={12} /> เลื่อนหรือซูมแผนที่ใดก็ได้ — มุมมองทั้งสามจะซิงก์กัน</div>
    </motion.div>
  );
}

function AreaPanel({ area, loading, onOptimize }: { area: AreaProperties | null; loading: boolean; onOptimize: () => void }) {
  if (loading) return <div className="empty-state"><Activity className="animate-pulse text-cyan" /><LoadingLine /><p>กำลังเชื่อมข้อมูลที่ผ่านการตรวจสอบ</p></div>;
  if (!area) return <div className="empty-state"><MapIcon /><h2>เลือกพื้นที่บนแผนที่</h2><p>คลิกเขตเพื่อดูข้อมูลจริง วันที่สังเกตการณ์ และเหตุผลของคะแนน</p></div>;
  const isDemo = area.quality_status === "demo_only";
  return (
    <>
      <div className="area-heading">
        <div><span>AREA ANALYSIS</span><h2>{area.name_th}</h2><p>{area.name_en}</p></div>
        <div className="score-ring"><strong>{formatNumber(area.priority_score)}</strong><span>/100</span></div>
      </div>
      {isDemo && <div className="inline-warning">พื้นที่ตัวอย่าง — ไม่มีค่าการวัดสมมติ</div>}
      <div className="observation-strip"><Clock3 size={13} /><span>สังเกตการณ์จากแหล่งต้นทาง</span><strong>{formatThaiDate(area.observation_date)}</strong></div>
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
      <div className="source-note">ประชากร: ปีอ้างอิง {area.population_reference_year || "ยังไม่ระบุ"} · คุณภาพ: {area.quality_status || "ยังไม่ระบุ"}</div>
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
  onExport: () => void;
  loading: boolean;
  exporting: boolean;
  result: OptimizationResult | null;
  scenarios: OptimizationResult[];
  activeScenarioId: string | null;
  onScenarioSelect: (runId: string) => void;
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
      <Button className="w-full" variant="thermal" disabled={props.loading || props.disabled || !props.interventions.length} onClick={props.onRun}>
        {props.loading ? <RefreshCw className="animate-spin" size={15} /> : <Sparkles size={15} />} {props.loading ? "กำลังแก้ปัญหา…" : "คำนวณพอร์ตที่เหมาะสม"}
      </Button>
      <Button className="mt-2 w-full" variant="outline" disabled={props.loading || props.disabled} onClick={props.onCompare}><FlaskConical size={15} /> เทียบ 3 ยุทธศาสตร์</Button>
      {props.result && (
        <>
          <Card className="result-card">
            <div><span>ใช้งบ</span><strong>{formatBaht(props.result.total_cost_thb)}</strong></div>
            <div><span>ผู้รับประโยชน์โดยประมาณ</span><strong>{formatNumber(props.result.estimated_beneficiaries)}</strong></div>
            <div><span>โครงการที่เลือก</span><strong>{props.result.allocations.length}</strong></div>
            {props.result.is_outdated && <p className="inline-warning">มีชุดข้อมูลใหม่กว่า กรุณาคำนวณอีกครั้ง</p>}
          </Card>
          <Button className="mt-2 w-full" variant="outline" disabled={props.exporting} onClick={props.onExport}>
            {props.exporting ? <RefreshCw className="animate-spin" size={15} /> : <Download size={15} />}
            {props.exporting ? "กำลังสร้างรายงาน…" : "ส่งออกรายงานการตัดสินใจ"}
          </Button>
        </>
      )}
      {!!props.scenarios.length && (
        <div className="scenario-mini-list">
          {props.scenarios.map((item) => (
            <button className={cn(item.run_id === props.activeScenarioId && "active")} key={item.run_id} onClick={() => props.onScenarioSelect(item.run_id)}>
              <span>{item.strategy}<small>{item.allocations.length} โครงการ · {formatBaht(item.total_cost_thb)}</small></span>
              <strong>{formatNumber(item.estimated_beneficiaries)} คน</strong>
            </button>
          ))}
        </div>
      )}
    </>
  );
}

function AssistantPanel({ area, question, setQuestion, answer, loading, disabled, onAsk, error }: {
  area: AreaProperties | null;
  question: string;
  setQuestion: (value: string) => void;
  answer: string | null;
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
              <XAxis dataKey="date" tick={{ fill: "#78909d", fontSize: 9 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fill: "#78909d", fontSize: 9 }} tickLine={false} axisLine={false} />
              <Tooltip contentStyle={{ background: "#0d1922", border: "1px solid rgba(255,255,255,.12)", borderRadius: 8, fontSize: 11 }} />
              <Line type="monotone" dataKey="temperature_c" stroke="#ff5a36" strokeWidth={2} dot={false} connectNulls />
              <Line type="monotone" dataKey="priority_score" stroke="#4eeaff" strokeWidth={2} dot={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        ) : <div className="chart-empty"><History /> ยังไม่มีประวัติที่ผ่านการตรวจสอบสำหรับพื้นที่นี้</div>}
      </div>
    </div>
  );
}

function ObservatoryOverlay({ datasets, snapshot, loading, onRefresh, refreshing, job, error }: {
  datasets: DatasetStatus[];
  snapshot: ObservatorySnapshot | null;
  loading: boolean;
  onRefresh: (source: string, operatorToken: string) => void;
  refreshing: boolean;
  job: { job_id: string; status: string } | null;
  error: string | null;
}) {
  const [source, setSource] = useState("open-meteo");
  const [operatorToken, setOperatorToken] = useState("");
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
    <motion.div className="map-overlay-panel observatory" initial={{ opacity: 0, scale: .98 }} animate={{ opacity: 1, scale: 1 }}>
      <div className="overlay-heading"><div><Badge>PIPELINE MONITOR</Badge><h2>สุขภาพระบบข้อมูล</h2><p>เวลาสังเกตการณ์และเวลาที่ระบบตรวจสอบถูกแสดงแยกกันเสมอ</p></div></div>
      <div className="pipeline-summary-grid">
        <Card><span>กำลังรอ / ทำงาน</span><strong>{queue.queued} / {queue.running}</strong><small>durable job queue</small></Card>
        <Card><span>Asset ที่พบ / ประมวลผล</span><strong>{summary.discovered_assets_latest_checks} / {summary.processed_assets_latest_checks}</strong><small>จาก source check ล่าสุด</small></Card>
        <Card className={summary.failed_jobs_total ? "has-error" : ""}><span>งานล้มเหลว</span><strong>{summary.failed_jobs_total}</strong><small>เก็บเหตุผลไว้ตรวจสอบ</small></Card>
        <Card><span>พื้นที่ที่มี indicator</span><strong>{formatNumber(coverage.percent, 1)}%</strong><small>{coverage.areas_with_indicators} / {coverage.geographic_areas} พื้นที่</small></Card>
      </div>
      <div className="refresh-controls">
        <label><span>แหล่งข้อมูล</span><select value={source} onChange={(event) => setSource(event.target.value)}><option value="open-meteo">Open-Meteo</option><option value="landsat">Landsat 8/9</option><option value="sentinel">Sentinel-2</option><option value="dynamic-world">Dynamic World</option><option value="worldpop">WorldPop</option><option value="osm">OpenStreetMap</option></select></label>
        <label><span>รหัสผู้ดูแล (ส่งเฉพาะ server)</span><input type="password" autoComplete="current-password" value={operatorToken} onChange={(event) => setOperatorToken(event.target.value)} placeholder="DASHBOARD_ADMIN_TOKEN" /></label>
        <Button onClick={() => onRefresh(source, operatorToken)} disabled={refreshing || !operatorToken} variant="outline">{refreshing ? <RefreshCw className="animate-spin" size={14} /> : <RefreshCw size={14} />} ตรวจสอบตอนนี้</Button>
      </div>
      {error && <div className="error-box">{error}</div>}
      {job && <div className="job-box"><Activity size={14} /><span>Job {job.job_id}</span><strong>{job.status}</strong></div>}
      {loading && <div className="observatory-loading"><RefreshCw className="animate-spin" size={14} /> กำลังโหลดสถานะ pipeline</div>}
      <div className="dataset-grid">
        {datasets.map((item) => (
          <Card key={item.dataset_id} className="dataset-card">
            <div className="dataset-title"><strong>{item.source_name}</strong><StatusDot status={item.status} /></div>
            <dl>
              <div><dt>สังเกตการณ์ต้นทาง</dt><dd>{formatThaiDate(item.last_source_observed_at, true)}</dd></div>
              <div><dt>ระบบตรวจล่าสุด</dt><dd>{formatThaiDate(item.last_checked_at, true)}</dd></div>
              <div><dt>เผยแพร่ล่าสุด</dt><dd>{formatThaiDate(item.last_published_at, true)}</dd></div>
              <div><dt>ตรวจครั้งถัดไป</dt><dd>{formatThaiDate(item.next_check_at, true)}</dd></div>
            </dl>
            <p>{item.status_reason || `คุณภาพ: ${item.quality_status || "ยังไม่ระบุ"}`}</p>
          </Card>
        ))}
      </div>
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
