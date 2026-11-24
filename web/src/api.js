// Small fetch helpers shared by every screen. All paths go through /api (nginx in Docker, Vite in dev), so the
// session cookie is same-origin and HttpOnly: this code never sees it. State-changing requests carry the CSRF
// token the api returned from /auth/me (kept in memory only). A 401 tells the app the session has ended.

let csrfToken = "";
export function setCsrf(token) { csrfToken = token || ""; }

function detailText(data, status) {
  const d = data?.detail;
  if (typeof d === "string") return d;
  if (d?.message) {
    const more = (d.errors || []).map((e) => e.message).join(" ");
    return `${d.message}${more ? ` ${more}` : ""}`;
  }
  if (Array.isArray(d)) return d.map((e) => e.msg).join("; ");
  return `HTTP ${status}`;
}

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

function sessionEnded(status, path) {
  if (status === 401 && !path.startsWith("/auth/login") && !path.startsWith("/auth/register")) {
    window.dispatchEvent(new CustomEvent("session-ended"));
  }
}

export function headers(method, json) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) h["X-CSRF-Token"] = csrfToken;
  return h;
}

export async function api(path, { method = "GET", body, form } = {}) {
  const options = { method, credentials: "same-origin", headers: headers(method, body !== undefined && !form) };
  if (form) options.body = form;
  else if (body !== undefined) options.body = JSON.stringify(body);
  const res = await fetch(`/api${path}`, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    sessionEnded(res.status, path);
    throw new ApiError(detailText(data, res.status), res.status, data);
  }
  return data;
}

// Upload with real progress (fetch has no upload progress): onProgress(0..1) while the bytes are sent;
// after that the api reads and embeds the file before it answers.
export function upload(path, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    xhr.withCredentials = true;
    if (csrfToken) xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.upload.onload = () => onProgress?.(1);
    xhr.onerror = () => reject(new ApiError("Network error while uploading.", 0, null));
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else {
        sessionEnded(xhr.status, path);
        reject(new ApiError(detailText(data, xhr.status), xhr.status, data));
      }
    };
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}
