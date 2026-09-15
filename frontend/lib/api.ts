import type { Business, CallExtraction, CallSummary, TokenResponse } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `Request failed with status ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export function getBusiness(businessId: string): Promise<Business> {
  return request<Business>(`/api/business/${encodeURIComponent(businessId)}`);
}

export function createToken(payload: {
  business_id?: string;
  identity?: string;
}): Promise<TokenResponse> {
  return request<TokenResponse>(`/api/token`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listCallsByRoom(roomName: string): Promise<CallSummary[]> {
  return request<CallSummary[]>(
    `/api/calls?room_name=${encodeURIComponent(roomName)}&limit=1`,
  );
}

export function getCallExtraction(callId: string): Promise<CallExtraction> {
  return request<CallExtraction>(`/api/calls/${encodeURIComponent(callId)}/extraction`);
}

export { ApiError };
