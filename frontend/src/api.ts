import type { Listing, SearchConfig, SessionId, SessionSummary, Status } from "./types";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error ?? `Ошибка запроса: ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

export function getListings(session: SessionId, includeRejected: boolean): Promise<Listing[]> {
  const params = new URLSearchParams({ session: String(session) });
  if (includeRejected) params.set("include_rejected", "1");
  return requestJson<Listing[]>(`/api/listings?${params}`);
}

export function getStatus(session: SessionId): Promise<Status> {
  return requestJson<Status>(`/api/status?session=${session}`);
}

export function getSessions(): Promise<SessionSummary[]> {
  return requestJson<SessionSummary[]>("/api/sessions");
}

export function getConfig(session: SessionId): Promise<SearchConfig> {
  return requestJson<SearchConfig>(`/api/config?session=${session}`);
}

export function saveConfig(session: SessionId, cfg: Partial<SearchConfig>): Promise<SearchConfig> {
  return requestJson<SearchConfig>(`/api/config?session=${session}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
}

export function rescore(session: SessionId): Promise<{ rescored: number }> {
  return requestJson<{ rescored: number }>(`/api/rescore?session=${session}`, { method: "POST" });
}

export function pauseWorker(session: SessionId): Promise<Status> {
  return requestJson<Status>(`/api/worker/pause?session=${session}`, { method: "POST" });
}

export function resumeWorker(session: SessionId): Promise<Status> {
  return requestJson<Status>(`/api/worker/resume?session=${session}`, { method: "POST" });
}
