const TOKEN = document.querySelector('meta[name="netpulse-token"]')?.content ?? "";

export class ApiError extends Error {
  constructor(code, status, details = {}) {
    super(code);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

async function request(path, { method = "GET", body, timeout = 15000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = { "X-NetPulse-Token": TOKEN };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
  } catch {
    throw new ApiError("offline", 0);
  } finally {
    clearTimeout(timer);
  }
  if (response.status === 403) {
    const payload = await response.json().catch(() => ({}));
    if (payload.error === "token") {
      // The backend restarted (for example after elevation) and issued a new token.
      window.location.reload();
    }
    throw new ApiError(payload.error || "forbidden", 403);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const { error, ...details } = payload;
    throw new ApiError(error || "http_error", response.status, details);
  }
  return response;
}

export const api = {
  get: async (path) => (await request(path)).json(),
  post: async (path, body = {}, options = {}) => (await request(path, { method: "POST", body, ...options })).json(),
  text: async (path) => (await request(path)).text(),
  iconUrl: (key) => `/api/icon?key=${encodeURIComponent(key)}&t=${encodeURIComponent(TOKEN)}`,
};
