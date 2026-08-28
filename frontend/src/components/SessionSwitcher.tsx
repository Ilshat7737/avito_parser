import type { Status } from "../types";
import { StatusPill, StatusPillIndicator, StatusPillLabel } from "@/components/ui/status-pill";

// Стилистика подсмотрена у "Segmented Control Tabs" (21st.dev) — сегмент-
// контрол с активной пилюлей — но переключение оставлено обычными
// ссылками ?session=N (полная перезагрузка), а не клиентским табом без
// перезагрузки: сессия читается один раз при монтировании (App.tsx), и
// менять этот механизм — уже не чисто визуальная правка, а логика.
type SessionStatusVariant = "success" | "error" | "warning" | "info" | "default";

function describeStatus(status: Status | null): { variant: SessionStatusVariant; label: string } {
  if (status === null) return { variant: "default", label: "…" };
  if (status.paused && status.pause_reason === "blocked") return { variant: "error", label: "Капча" };
  if (status.paused && status.pause_reason === "error") return { variant: "error", label: "Ошибка" };
  if (status.paused) return { variant: "warning", label: "Пауза" };
  if (status.scanning) return { variant: "info", label: "Сканирую" };
  return { variant: "success", label: "Готово" };
}

function SessionTab({
  sessionId,
  active,
  status,
}: {
  sessionId: 1 | 2;
  active: boolean;
  status: Status | null;
}) {
  const { variant, label } = describeStatus(status);
  // Полные литеральные классы для каждой сессии — Tailwind JIT не видит
  // классы, собранные шаблонной строкой из переменной (bg-${accent}),
  // их нужно писать целиком, чтобы попали в сборку.
  const activeClasses =
    sessionId === 1
      ? "bg-session1/15 text-session1 ring-1 ring-inset ring-session1/40"
      : "bg-session2/15 text-session2 ring-1 ring-inset ring-session2/40";
  const dotClasses = sessionId === 1 ? "bg-session1" : "bg-session2";

  return (
    <a
      href={`?session=${sessionId}`}
      aria-current={active ? "page" : undefined}
      className={`flex items-center gap-2.5 rounded-full px-3.5 py-2 text-sm font-medium transition-colors ${
        active ? activeClasses : "text-muted-foreground hover:bg-white/5 hover:text-foreground"
      }`}
    >
      <span className={`size-2 rounded-full ${dotClasses}`} aria-hidden />
      Сессия {sessionId}
      <StatusPill variant={variant} className="ml-0.5">
        <StatusPillIndicator />
        <StatusPillLabel>{label}</StatusPillLabel>
      </StatusPill>
    </a>
  );
}

export function SessionSwitcher({
  activeSessionId,
  ownStatus,
  otherStatus,
}: {
  activeSessionId: 1 | 2;
  ownStatus: Status;
  otherStatus: Status | null;
}) {
  const session1Status = activeSessionId === 1 ? ownStatus : otherStatus;
  const session2Status = activeSessionId === 2 ? ownStatus : otherStatus;

  return (
    <div className="inline-flex items-center gap-1 rounded-full border border-border bg-card/60 p-1">
      <SessionTab sessionId={1} active={activeSessionId === 1} status={session1Status} />
      <SessionTab sessionId={2} active={activeSessionId === 2} status={session2Status} />
    </div>
  );
}
