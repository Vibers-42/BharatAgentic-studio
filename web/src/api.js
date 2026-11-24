// Small fetch helpers shared by every screen. All paths go through /api (nginx in Docker, Vite in dev).

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

export async function api(path, { method = "GET", body, form } = {}) {
  const options = { method };
  if (form) options.body = form;
  else if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const res = await fetch(`/api${path}`, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(detailText(data, res.status), res.status, data);
  return data;
}

// Upload with real progress (fetch has no upload progress): onProgress(0..1) while the bytes are sent;
// after that the api reads and embeds the file before it answers.
export function upload(path, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api${path}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.upload.onload = () => onProgress?.(1);
    xhr.onerror = () => reject(new ApiError("Network error while uploading.", 0, null));
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(detailText(data, xhr.status), xhr.status, data));
    };
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}
