import { cn, freshnessLabel, type FreshnessState } from "@/lib/utils";

const colors: Record<FreshnessState, string> = {
  up_to_date: "bg-emerald shadow-[0_0_9px_#56e39f]",
  updating: "bg-cyan shadow-[0_0_9px_#4eeaff] animate-pulse",
  stale: "bg-amber-400 shadow-[0_0_9px_#fbbf24]",
  unavailable: "bg-slate-500",
  error: "bg-red-500 shadow-[0_0_9px_#ef4444]",
};

export function StatusDot({ status, label = true }: { status: FreshnessState; label?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-slate-300">
      <span aria-hidden className={cn("h-1.5 w-1.5 rounded-full", colors[status])} />
      {label ? freshnessLabel(status) : <span className="sr-only">{freshnessLabel(status)}</span>}
    </span>
  );
}
