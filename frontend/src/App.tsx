import { useCallback, useEffect, useRef, useState } from "react";
import { Toaster, toast } from "sonner";
import { AnimatePresence, MotionConfig } from "framer-motion";
import { getConfig, getListings, getStatus, rescore, saveConfig } from "./api";
import type { Listing, SearchConfig, Status } from "./types";
import { ListingCard } from "./components/ListingCard";
import { SearchForm } from "./components/SearchForm";
import { StatusBar } from "./components/StatusBar";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";

const POLL_INTERVAL_MS = 60_000;

export default function App() {
  const [listings, setListings] = useState<Listing[]>([]);
  const [status, setStatus] = useState<Status>({ last_check: null, last_count: 0 });
  const [config, setConfig] = useState<Partial<SearchConfig>>({});
  const [showRejected, setShowRejected] = useState(false);
  const [rescoring, setRescoring] = useState(false);
  const [loading, setLoading] = useState(true);
  const knownIdsRef = useRef<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    const [freshListings, freshStatus] = await Promise.all([getListings(showRejected), getStatus()]);
    const newRelevant = freshListings.filter((l) => l.relevant === 1 && !knownIdsRef.current.has(l.id));
    for (const item of newRelevant) {
      toast.success(`Новое объявление: ${item.title}`);
    }
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

  return (
    <MotionConfig reducedMotion="user">
      <div className="min-h-screen p-6 max-w-6xl mx-auto grid gap-6">
        <Toaster richColors position="top-right" />
        <h1 className="text-2xl font-semibold">Поиск машин на Avito</h1>
        <SearchForm initial={config} onSave={handleSave} />
        <div className="flex items-center justify-between flex-wrap gap-3">
          <StatusBar status={status} />
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={showRejected} onCheckedChange={setShowRejected} />
              Показать отклонённые агентом
            </label>
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
