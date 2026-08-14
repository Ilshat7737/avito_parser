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
      className={`text-sm font-mono px-3 py-2 rounded-lg ${
        status.paused ? "bg-white/10" : stale ? "bg-danger/20 text-danger" : "bg-white/5"
      }`}
    >
      Последняя проверка: {status.last_check ?? "ещё не было"} · Найдено: {status.last_count}
      {status.paused && " · ⏸ парсинг приостановлен"}
      {!status.paused && stale && " · фоновый поток мог остановиться"}
    </div>
  );
}
