import type { SearchConfig } from "../types";

function formatRange(min: number | null | undefined, max: number | null | undefined, unit: string): string | null {
  if (min == null && max == null) return null;
  const fmt = (n: number) => n.toLocaleString("ru-RU");
  if (min != null && max != null) return `${fmt(min)}–${fmt(max)}${unit ? ` ${unit}` : ""}`;
  if (min != null) return `от ${fmt(min)}${unit ? ` ${unit}` : ""}`;
  return `до ${fmt(max as number)}${unit ? ` ${unit}` : ""}`;
}

export function FilterSummary({ config }: { config: Partial<SearchConfig> }) {
  if (!config.search_url) {
    return (
      <div className="glass-panel rounded-xl px-4 py-3 text-sm text-[hsl(var(--foreground))]/70">
        Поиск ещё не настроен — заполните форму ниже и нажмите «Сохранить».
      </div>
    );
  }

  const priceRange = formatRange(config.price_min, config.price_max, "₽");
  const mileageRange = formatRange(config.mileage_min, config.mileage_max, "км");
  const yearRange = formatRange(config.year_min, config.year_max, "");

  const parts = [
    config.city && `Город: ${config.city}`,
    config.brand && `Марка: ${config.brand}`,
    priceRange && `Цена: ${priceRange}`,
    mileageRange && `Пробег: ${mileageRange}`,
    yearRange && `Год: ${yearRange}`,
  ].filter(Boolean) as string[];

  return (
    <div className="glass-panel rounded-xl px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-[hsl(var(--foreground))]/50 mb-1">
        Активный поиск
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm font-mono">
        {parts.length > 0 ? (
          parts.map((p) => <span key={p}>{p}</span>)
        ) : (
          <span>Без дополнительных фильтров</span>
        )}
      </div>
      {config.query_text && (
        <div className="text-sm text-[hsl(var(--foreground))]/70 mt-1">«{config.query_text}»</div>
      )}
    </div>
  );
}
