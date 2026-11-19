import type { Session } from "./types";

/** The access token lives only in memory. A page script cannot read the refresh
 *  token at all, because it stays in an httpOnly cookie. */
let accessToken: string | null = null;
let onSessionLost: (() => void) | null = null;
let refreshing: Promise<Session | null> | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function setSessionLostHandler(handler: (() => void) | null): void {
  onSessionLost = handler;
}

export class ApiError extends Error {
  readonly status: number;
  readonly correlationId: string | null;

  constructor(status: number, message: string, correlationId: string | null = null) {
    super(message);
    this.status = status;
    this.correlationId = correlationId;
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item: { loc?: unknown[]; msg?: string }) => {
        const field = (item.loc ?? []).filter((part) => part !== "body").join(".");
        return field ? `${field}: ${item.msg}` : item.msg;
      })
      .join("; ");
  }
  return "Something went wrong";
}

async function toError(response: Response): Promise<ApiError> {
  try {
    const body = await response.json();
    return new ApiError(response.status, describe(body.detail), body.correlation_id ?? null);
  } catch {
    return new ApiError(response.status, `Request failed with status ${response.status}`);
  }
}

async function postRefresh(): Promise<Session | null> {
  const response = await fetch("/v1/auth/refresh", { method: "POST", credentials: "same-origin" });
  if (!response.ok) return null;
  const session = (await response.json()) as Session;
  accessToken = session.access_token;
  return session;
}

/** Refresh tokens rotate and replaying one ends the session, so refreshes must
 *  never overlap: one at a time in this tab, and across tabs through Web Locks. */
export function refreshSession(): Promise<Session | null> {
  if (!refreshing) {
    const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
    const run = locks ? locks.request("aa-refresh", postRefresh) : postRefresh();
    refreshing = Promise.resolve(run).finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

function send(path: string, options: RequestOptions): Promise<Response> {
  const headers = new Headers(options.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  let body: BodyInit | undefined;
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }
  return fetch(path, { ...options, headers, body, credentials: "same-origin" });
}

export async function apiResponse(path: string, options: RequestOptions = {}): Promise<Response> {
  let response = await send(path, options);
  if (response.status === 401 && !path.startsWith("/v1/auth/")) {
    if (await refreshSession()) {
      response = await send(path, options);
    } else {
      onSessionLost?.();
    }
  }
  if (!response.ok) throw await toError(response);
  return response;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await apiResponse(path, options);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** Downloads through fetch, so the bearer token never goes in a URL. */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const response = await apiResponse(path);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
