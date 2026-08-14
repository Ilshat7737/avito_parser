import type { Listing, SearchConfig, Status } from "./types";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error ?? `Ошибка запроса: ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

export function getListings(includeRejected: boolean): Promise<Listing[]> {
  const query = includeRejected ? "?include_rejected=1" : "";
  return requestJson<Listing[]>(`/api/listings${query}`);
}

export function getStatus(): Promise<Status> {
  return requestJson<Status>("/api/status");
}

export function getConfig(): Promise<SearchConfig> {
  return requestJson<SearchConfig>("/api/config");
}

export function saveConfig(cfg: Partial<SearchConfig>): Promise<SearchConfig> {
  return requestJson<SearchConfig>("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
}

export function rescore(): Promise<{ rescored: number }> {
  return requestJson<{ rescored: number }>("/api/rescore", { method: "POST" });
}

export function pauseWorker(): Promise<Status> {
  return requestJson<Status>("/api/worker/pause", { method: "POST" });
}

export function resumeWorker(): Promise<Status> {
  return requestJson<Status>("/api/worker/resume", { method: "POST" });
}
