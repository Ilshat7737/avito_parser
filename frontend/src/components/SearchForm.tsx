import { useEffect, useState } from "react";
import type { SearchConfig, SessionId } from "../types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  session: SessionId;
  initial: Partial<SearchConfig>;
  onSave: (cfg: Partial<SearchConfig>) => Promise<void>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="text-xs font-medium uppercase tracking-wide text-[hsl(var(--foreground))]/50">{label}</span>
      {children}
    </label>
  );
}

export function SearchForm({ session, initial, onSave }: Props) {
  const [form, setForm] = useState<Partial<SearchConfig>>(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setForm(initial);
  }, [initial]);

  const field = (key: keyof SearchConfig) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setForm((prev) => ({
      ...prev,
      [key]: value === "" ? null : ["city", "brand", "query_text", "label"].includes(key) ? value : Number(value),
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await onSave(form);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сохранить настройки");
    } finally {
      setSaving(false);
    }
  };

  const accentVar = session === 1 ? "--session-1" : "--session-2";

  return (
    <form onSubmit={handleSubmit} className="glass-panel grid gap-4 rounded-2xl p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold" style={{ color: `hsl(var(${accentVar}))` }}>
          Фильтры окна {session}
        </h2>
      </div>

      <Field label="Название окна (необязательно)">
        <Input
          placeholder={`Например: «Окно ${session}: семейный седан»`}
          value={form.label ?? ""}
          onChange={field("label")}
        />
      </Field>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Город">
          <Input placeholder="напр. kazan (латиницей)" value={form.city ?? ""} onChange={field("city")} required />
        </Field>
        <Field label="Марка">
          <Input placeholder="напр. Kia" value={form.brand ?? ""} onChange={field("brand")} />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field label="Цена от">
          <Input type="number" value={form.price_min ?? ""} onChange={field("price_min")} />
        </Field>
        <Field label="Цена до">
          <Input type="number" value={form.price_max ?? ""} onChange={field("price_max")} />
        </Field>
        <Field label="Год от">
          <Input type="number" value={form.year_min ?? ""} onChange={field("year_min")} />
        </Field>
        <Field label="Год до">
          <Input type="number" value={form.year_max ?? ""} onChange={field("year_max")} />
        </Field>
        <Field label="Пробег от">
          <Input type="number" value={form.mileage_min ?? ""} onChange={field("mileage_min")} />
        </Field>
        <Field label="Пробег до">
          <Input type="number" value={form.mileage_max ?? ""} onChange={field("mileage_max")} />
        </Field>
      </div>

      <Field label="Текстовое уточнение для LLM-агента">
        <Textarea
          placeholder="Например: для семьи, не битая, механика"
          value={form.query_text ?? ""}
          onChange={(e) => setForm((prev) => ({ ...prev, query_text: e.target.value }))}
        />
      </Field>

      {error && <p className="text-danger text-sm">{error}</p>}
      <Button type="submit" disabled={saving} className="justify-self-start">
        {saving ? "Собираю ссылку поиска…" : `Сохранить фильтры окна ${session}`}
      </Button>
    </form>
  );
}
