import { useCallback, useEffect, useRef, useState } from "react";
import { Toaster, toast } from "sonner";
import { AnimatePresence, MotionConfig } from "framer-motion";
import { getConfig, getListings, getSessions, getStatus, pauseWorker, rescore, resumeWorker, saveConfig } from "./api";
import type { Listing, SearchConfig, SessionId, SessionSummary, Status } from "./types";
import { FilterSummary } from "./components/FilterSummary";
import { ListingCard } from "./components/ListingCard";
import { SearchForm } from "./components/SearchForm";
import { SessionTabs } from "./components/SessionTabs";
import { StatusBar } from "./components/StatusBar";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";

// Опрос собственного локального API (не Avito) — можно часто, чтобы уже
// оценённые агентом объявления появлялись на экране почти сразу. Интервал
// самого скрапинга Avito (CHECK_INTERVAL_SEC в config.py) от этого не
// зависит и не меняется.
const POLL_INTERVAL_MS = 3_000;

const EMPTY_STATUS: Status = {
  last_check: null, last_count: 0, paused: false,
  pause_reason: null, last_error: null, scanning: false,
};

export default function App() {
  const [activeSession, setActiveSession] = useState<SessionId>(1);
  const [sessionSummaries, setSessionSummaries] = useState<Record<SessionId, SessionSummary | undefined>>({
    1: undefined,
    2: undefined,
  });

  const [listings, setListings] = useState<Listing[]>([]);
  const [status, setStatus] = useState<Status>(EMPTY_STATUS);
  const [config, setConfig] = useState<Partial<SearchConfig>>({});
  const [showRejected, setShowRejected] = useState(false);
  const [rescoring, setRescoring] = useState(false);
  const [pauseToggling, setPauseToggling] = useState(false);
  const [loading, setLoading] = useState(true);

  const knownIdsRef = useRef<Record<SessionId, Set<string>>>({ 1: new Set(), 2: new Set() });
  const hasLoadedOnceRef = useRef<Record<SessionId, boolean>>({ 1: false, 2: false });

  // Сводка по обеим сессиям сразу — нужна переключателю вкладок, чтобы
  // показывать живой статус (сканирует/пауза/ошибка) окна, на которое
  // сейчас не смотрит пользователь. Работает независимо от того, какая
  // вкладка активна.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const rows = await getSessions();
        if (cancelled) return;
        setSessionSummaries({
          1: rows.find((r) => r.session_id === 1),
          2: rows.find((r) => r.session_id === 2),
        });
      } catch {
        // тихо игнорируем — обычный опрос статуса, не критично
      }
    };
    poll();
    const id = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const refresh = useCallback(async (session: SessionId, withRejected: boolean) => {
    const [freshListings, freshStatus] = await Promise.all([getListings(session, withRejected), getStatus(session)]);
    return { session, freshListings, freshStatus };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getConfig(activeSession).then((cfg) => {
      if (!cancelled) setConfig(cfg);
    });

    const poll = async () => {
      const { session, freshListings, freshStatus } = await refresh(activeSession, showRejected);
      if (cancelled || session !== activeSession) return;

      if (hasLoadedOnceRef.current[session]) {
        const newRelevant = freshListings.filter(
          (l) => l.relevant === 1 && !knownIdsRef.current[session].has(l.id)
        );
        for (const item of newRelevant) {
          toast.success(`[Окно ${session}] Новое объявление: ${item.title}`);
        }
      }
      hasLoadedOnceRef.current[session] = true;
      knownIdsRef.current[session] = new Set(freshListings.map((l) => l.id));

      setListings(freshListings);
      setStatus(freshStatus);
      setLoading(false);
    };

    poll();
    const id = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [activeSession, showRejected, refresh]);

  const handleSave = async (cfg: Partial<SearchConfig>) => {
    const saved = await saveConfig(activeSession, cfg);
    setConfig(saved);
    const { freshListings, freshStatus } = await refresh(activeSession, showRejected);
    setListings(freshListings);
    setStatus(freshStatus);
  };

  const handleRescore = async () => {
    setRescoring(true);
    try {
      const result = await rescore(activeSession);
      toast.info(`[Окно ${activeSession}] Переоценено объявлений: ${result.rescored}`);
      const { freshListings, freshStatus } = await refresh(activeSession, showRejected);
      setListings(freshListings);
      setStatus(freshStatus);
    } finally {
      setRescoring(false);
    }
  };

  const handleTogglePause = async () => {
    setPauseToggling(true);
    try {
      if (status.paused) {
        await resumeWorker(activeSession);
      } else {
        await pauseWorker(activeSession);
      }
      const { freshStatus } = await refresh(activeSession, showRejected);
      setStatus(freshStatus);
    } finally {
      setPauseToggling(false);
    }
  };

  return (
    <MotionConfig reducedMotion="user">
      <div className="min-h-screen px-4 py-8 sm:px-6">
        <Toaster richColors position="top-right" />
        <div className="mx-auto grid max-w-6xl gap-6">
          <header className="grid gap-1">
            <h1 className="text-3xl font-semibold tracking-tight">
              <span className="text-gradient">Avito Radar</span>
            </h1>
            <p className="text-sm text-[hsl(var(--foreground))]/60">
              Два независимых окна поиска работают параллельно, каждое — со своими фильтрами.
              Объявление, которое уже забрало соседнее окно за последние 24 часа, повторно не обрабатывается.
            </p>
          </header>

          <SessionTabs active={activeSession} onChange={setActiveSession} summaries={sessionSummaries} />

          {status.pause_reason === "blocked" && (
            <div className="rounded-xl bg-danger/15 border border-danger/40 text-danger px-4 py-3 text-sm">
              ⚠️ Avito показывает капчу или заблокировал доступ (окно {activeSession}). Решите капчу в
              открытом окне браузера (если оно ещё открыто) или подождите,
              затем нажмите «Возобновить».
            </div>
          )}
          {status.pause_reason === "error" && (
            <div className="rounded-xl bg-danger/15 border border-danger/40 text-danger px-4 py-3 text-sm">
              ⚠️ Цикл сканирования окна {activeSession} завершился с ошибкой и приостановлен —
              повторять автоматически не стал, чтобы не долбить сайт впустую.
              Проверьте, что происходит, и нажмите «Возобновить», когда
              будете готовы попробовать снова.
              {status.last_error && (
                <div className="mt-1 font-mono text-xs opacity-80 break-all">{status.last_error}</div>
              )}
            </div>
          )}

          <FilterSummary config={config} />
          <SearchForm session={activeSession} initial={config} onSave={handleSave} />

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
                <Skeleton key={i} className="h-64 rounded-2xl" />
              ))
            ) : listings.length === 0 ? (
              <div className="col-span-full glass-panel rounded-2xl px-6 py-10 text-center text-sm text-[hsl(var(--foreground))]/60">
                Пока ничего не найдено в окне {activeSession}. Как только фоновый поток
                просканирует Avito, подходящие объявления появятся здесь.
              </div>
            ) : (
              <AnimatePresence>
                {listings.map((listing) => (
                  <ListingCard key={listing.id} listing={listing} session={activeSession} />
                ))}
              </AnimatePresence>
            )}
          </div>
        </div>
      </div>
    </MotionConfig>
  );
}
