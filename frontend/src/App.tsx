import { useCallback, useEffect, useRef, useState } from "react";
import { Toaster, toast } from "sonner";
import { AnimatePresence, MotionConfig } from "framer-motion";
import { getConfig, getListings, getStatus, pauseWorker, rescore, resumeWorker, saveConfig } from "./api";
import type { Listing, SearchConfig, Status } from "./types";
import { FilterSummary } from "./components/FilterSummary";
import { ListingCard } from "./components/ListingCard";
import { SearchForm } from "./components/SearchForm";
import { StatusBar } from "./components/StatusBar";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";

// Опрос собственного локального API (не Avito) — можно часто, чтобы уже
// оценённые агентом объявления появлялись на экране почти сразу. Интервал
// самого скрапинга Avito (CHECK_INTERVAL_SEC в config.py) от этого не
// зависит и не меняется.
const POLL_INTERVAL_MS = 3_000;

export default function App() {
  const [listings, setListings] = useState<Listing[]>([]);
  const [status, setStatus] = useState<Status>({
    last_check: null, last_count: 0, paused: false,
    pause_reason: null, last_error: null, scanning: false,
  });
  const [config, setConfig] = useState<Partial<SearchConfig>>({});
  const [showRejected, setShowRejected] = useState(false);
  const [rescoring, setRescoring] = useState(false);
  const [pauseToggling, setPauseToggling] = useState(false);
  const [loading, setLoading] = useState(true);
  const knownIdsRef = useRef<Set<string>>(new Set());
  const hasLoadedOnceRef = useRef(false);

  const refresh = useCallback(async () => {
    const [freshListings, freshStatus] = await Promise.all([getListings(showRejected), getStatus()]);
    if (hasLoadedOnceRef.current) {
      const newRelevant = freshListings.filter((l) => l.relevant === 1 && !knownIdsRef.current.has(l.id));
      for (const item of newRelevant) {
        toast.success(`Новое объявление: ${item.title}`);
      }
    }
    hasLoadedOnceRef.current = true;
    knownIdsRef.current = new Set(freshListings.map((l) => l.id));
    setListings(freshListings);
    setStatus(freshStatus);
    setLoading(false);
  }, [showRejected]);

  useEffect(() => {
    getConfig().then(setConfig);
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [showRejected, refresh]);

  const handleSave = async (cfg: Partial<SearchConfig>) => {
    const saved = await saveConfig(cfg);
    setConfig(saved);
    await refresh();
  };

  const handleRescore = async () => {
    setRescoring(true);
    try {
      const result = await rescore();
      toast.info(`Переоценено объявлений: ${result.rescored}`);
      await refresh();
    } finally {
      setRescoring(false);
    }
  };

  const handleTogglePause = async () => {
    setPauseToggling(true);
    try {
      if (status.paused) {
        await resumeWorker();
      } else {
        await pauseWorker();
      }
      await refresh();
    } finally {
      setPauseToggling(false);
    }
  };

  return (
    <MotionConfig reducedMotion="user">
      <div className="min-h-screen p-6 max-w-6xl mx-auto grid gap-6">
        <Toaster richColors position="top-right" />
        <h1 className="text-2xl font-semibold">Поиск машин на Avito</h1>
        {status.pause_reason === "blocked" && (
          <div className="rounded-xl bg-danger/20 border border-danger text-danger px-4 py-3 text-sm">
            ⚠️ Avito показывает капчу или заблокировал доступ. Решите капчу в
            открытом окне браузера (если оно ещё открыто) или подождите,
            затем нажмите «Возобновить».
          </div>
        )}
        {status.pause_reason === "error" && (
          <div className="rounded-xl bg-danger/20 border border-danger text-danger px-4 py-3 text-sm">
            ⚠️ Цикл сканирования завершился с ошибкой и приостановлен —
            повторять автоматически не стал, чтобы не долбить сайт впустую.
            Проверьте, что происходит, и нажмите «Возобновить», когда
            будете готовы попробовать снова.
            {status.last_error && (
              <div className="mt-1 font-mono text-xs opacity-80 break-all">{status.last_error}</div>
            )}
          </div>
        )}
        <FilterSummary config={config} />
        <SearchForm initial={config} onSave={handleSave} />
        <div className="flex items-center justify-between flex-wrap gap-3">
          <StatusBar status={status} />
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={showRejected} onCheckedChange={setShowRejected} />
              Показать отклонённые агентом
            </label>
            <Button variant="outline" onClick={handleTogglePause} disabled={pauseToggling}>
              {status.paused ? "▶ Возобновить" : "⏸ Остановить парсинг"}
            </Button>
            <Button variant="outline" onClick={handleRescore} disabled={rescoring}>
              {rescoring ? "Переоцениваю…" : "Переоценить"}
            </Button>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {loading ? (
            Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-64 rounded-xl" />
            ))
          ) : (
            <AnimatePresence>
              {listings.map((listing) => (
                <ListingCard key={listing.id} listing={listing} />
              ))}
            </AnimatePresence>
          )}
        </div>
      </div>
    </MotionConfig>
  );
}
