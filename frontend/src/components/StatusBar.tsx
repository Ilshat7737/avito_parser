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
      className={`flex items-center gap-2 text-sm font-mono px-3 py-2 rounded-lg ${
        status.paused ? "bg-white/10" : stale ? "bg-danger/20 text-danger" : "bg-white/5"
      }`}
    >
      {status.scanning && !status.paused && (
        <Loader2 className="h-4 w-4 animate-spin text-accent" aria-label="Идёт сканирование" />
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
