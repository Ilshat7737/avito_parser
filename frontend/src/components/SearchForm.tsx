import { useEffect, useState } from "react";
import type { SearchConfig } from "../types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface Props {
  initial: Partial<SearchConfig>;
  onSave: (cfg: Partial<SearchConfig>) => Promise<void>;
}

export function SearchForm({ initial, onSave }: Props) {
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
      [key]: value === "" ? null : ["city", "brand", "query_text"].includes(key) ? value : Number(value),
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

  return (
    <form onSubmit={handleSubmit} className="grid gap-3 rounded-xl bg-[hsl(var(--card))] p-4">
      <Input placeholder="Город (латиницей, напр. kazan)" value={form.city ?? ""} onChange={field("city")} required />
      <Input placeholder="Марка (напр. Kia)" value={form.brand ?? ""} onChange={field("brand")} />
      <div className="grid grid-cols-2 gap-3">
        <Input type="number" placeholder="Цена от" value={form.price_min ?? ""} onChange={field("price_min")} />
        <Input type="number" placeholder="Цена до" value={form.price_max ?? ""} onChange={field("price_max")} />
        <Input type="number" placeholder="Пробег от" value={form.mileage_min ?? ""} onChange={field("mileage_min")} />
        <Input type="number" placeholder="Пробег до" value={form.mileage_max ?? ""} onChange={field("mileage_max")} />
        <Input type="number" placeholder="Год от" value={form.year_min ?? ""} onChange={field("year_min")} />
        <Input type="number" placeholder="Год до" value={form.year_max ?? ""} onChange={field("year_max")} />
      </div>
      <Textarea
        placeholder="Текстовое уточнение (например: для семьи, не битая, механика)"
        value={form.query_text ?? ""}
        onChange={(e) => setForm((prev) => ({ ...prev, query_text: e.target.value }))}
      />
      {error && <p className="text-danger text-sm">{error}</p>}
      <Button type="submit" disabled={saving}>
        {saving ? "Собираю ссылку поиска…" : "Сохранить"}
      </Button>
    </form>
  );
}
