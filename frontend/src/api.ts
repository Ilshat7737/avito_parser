import type { Listing, SearchConfig, Status } from "./types";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error ?? `Ошибка запроса: ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

export function getListings(sessionId: number, includeRejected: boolean): Promise<Listing[]> {
  const params = new URLSearchParams({ session_id: String(sessionId) });
  if (includeRejected) params.set("include_rejected", "1");
  return requestJson<Listing[]>(`/api/listings?${params.toString()}`);
}

export function getStatus(sessionId: number): Promise<Status> {
  return requestJson<Status>(`/api/status?session_id=${sessionId}`);
}

export function getConfig(sessionId: number): Promise<SearchConfig> {
  return requestJson<SearchConfig>(`/api/config?session_id=${sessionId}`);
}

export function saveConfig(sessionId: number, cfg: Partial<SearchConfig>): Promise<SearchConfig> {
  return requestJson<SearchConfig>("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...cfg, session_id: sessionId }),
  });
}

export function rescore(sessionId: number): Promise<{ rescored: number }> {
  return requestJson<{ rescored: number }>("/api/rescore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export function pauseWorker(sessionId: number): Promise<Status> {
  return requestJson<Status>("/api/worker/pause", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export function resumeWorker(sessionId: number): Promise<Status> {
  return requestJson<Status>("/api/worker/resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
}
