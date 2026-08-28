import { Loader2 } from "lucide-react";
import type { Status } from "../types";

export function StatusBar({ status }: { status: Status }) {
  // При MIN_DELAY/MAX_DELAY = 20-30 сек между запросами страниц (config.py)
  // цикл с несколькими новыми объявлениями может легко занять 10+ минут —
  // порог "поток мог остановиться" должен быть заметно больше этого.
  const stale =
    !status.paused &&
    status.last_check !== null &&
    Date.now() - new Date(status.last_check).getTime() > 30 * 60 * 1000;

  return (
    <div
      className={`flex items-center gap-2 text-sm font-tabular px-3 py-2 rounded-full border ${
        status.paused
          ? "border-border bg-secondary text-muted-foreground"
          : stale
            ? "border-danger/30 bg-danger/10 text-danger"
            : "border-border bg-card text-foreground"
      }`}
    >
      {status.scanning && !status.paused && (
        <Loader2 className="h-4 w-4 animate-spin text-primary" aria-label="Идёт сканирование" />
      )}
      <span>
        Последняя проверка: {status.last_check ?? "ещё не было"} · Найдено: {status.last_count}
        {status.scanning && !status.paused && " · сканирую…"}
        {status.paused && " · ⏸ парсинг приостановлен"}
        {!status.paused && stale && " · фоновый поток мог остановиться"}
      </span>
    </div>
  );
}
