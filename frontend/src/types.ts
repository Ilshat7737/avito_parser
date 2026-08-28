export type SessionId = 1 | 2;

export interface Listing {
  id: string;
  title: string;
  price: number | null;
  url: string;
  photo: string | null;
  description: string | null;
  year: number | null;
  mileage: number | null;
  relevant: number | null;
  relevance_reason: string | null;
  agent_evaluated_at: string | null;
  first_seen: string;
  last_price: number | null;
  last_checked: string;
}

export interface SearchConfig {
  session_id: SessionId;
  label: string | null;
  city: string;
  brand: string | null;
  price_min: number | null;
  price_max: number | null;
  mileage_min: number | null;
  mileage_max: number | null;
  year_min: number | null;
  year_max: number | null;
  query_text: string | null;
  search_url: string | null;
  updated_at: string | null;
}

export interface Status {
  last_check: string | null;
  last_count: number;
  paused: boolean;
  pause_reason: string | null;
  last_error: string | null;
  scanning: boolean;
}

export interface SessionSummary {
  session_id: SessionId;
  config: Partial<SearchConfig> | null;
  status: Status;
}
