import type { Status } from "../types";

export function StatusBar({ status }: { status: Status }) {
  const stale =
    !status.paused &&
    status.last_check !== null &&
    Date.now() - new Date(status.last_check).getTime() > 5 * 60 * 1000;

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
