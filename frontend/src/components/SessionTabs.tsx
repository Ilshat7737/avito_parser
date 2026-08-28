import type { SessionId, SessionSummary } from "../types";

interface Props {
  active: SessionId;
  onChange: (session: SessionId) => void;
  summaries: Record<SessionId, SessionSummary | undefined>;
}

const SESSION_IDS: SessionId[] = [1, 2];

function dotClasses(summary: SessionSummary | undefined): string {
  if (!summary) return "bg-muted-foreground/40";
  const { status } = summary;
  if (status.paused) return "bg-amber-400";
  if (status.scanning) return "bg-emerald-400 animate-pulse";
  return "bg-muted-foreground/60";
}

function subtitle(summary: SessionSummary | undefined): string {
  if (!summary) return "Загрузка…";
  const { status, config } = summary;
  if (status.paused) return status.pause_reason === "error" ? "Остановлена: ошибка" : status.pause_reason === "blocked" ? "Остановлена: блокировка" : "На паузе";
  if (!config?.search_url) return "Поиск не настроен";
  if (status.scanning) return "Сканирует прямо сейчас…";
  return `Найдено: ${status.last_count}`;
}

export function SessionTabs({ active, onChange, summaries }: Props) {
  return (
    <div className="glass-panel flex flex-wrap gap-2 rounded-2xl p-1.5 shadow-sm">
      {SESSION_IDS.map((sid) => {
        const summary = summaries[sid];
        const label = summary?.config?.label?.trim() || `Окно ${sid}`;
        const isActive = sid === active;
        const accentVar = sid === 1 ? "--session-1" : "--session-2";
        return (
          <button
            key={sid}
            type="button"
            onClick={() => onChange(sid)}
            className={`group relative flex flex-1 min-w-[180px] items-center gap-3 rounded-xl px-4 py-3 text-left transition-all ${
              isActive ? "shadow-md" : "hover:bg-[hsl(var(--foreground))]/5"
            }`}
            style={
              isActive
                ? { backgroundColor: `hsl(var(${accentVar}) / 0.14)`, boxShadow: `inset 0 0 0 1px hsl(var(${accentVar}) / 0.4)` }
                : undefined
            }
          >
            <span
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-mono font-semibold"
              style={{ backgroundColor: `hsl(var(${accentVar}) / 0.18)`, color: `hsl(var(${accentVar}))` }}
            >
              {sid}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate font-medium text-sm">{label}</span>
                <span className={`h-2 w-2 shrink-0 rounded-full ${dotClasses(summary)}`} />
              </div>
              <div className="truncate text-xs text-[hsl(var(--foreground))]/60">{subtitle(summary)}</div>
            </div>
          </button>
        );
      })}
    </div>
  );
}
