const BASE = "";

async function json(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function startRun({ resumeFile, profile, minFitScore }) {
  const form = new FormData();
  form.append("resume", resumeFile);
  if (profile) form.append("profile", profile);
  form.append("min_fit_score", String(minFitScore));
  const res = await fetch(`${BASE}/api/run`, { method: "POST", body: form });
  return json(res);
}

export function streamRun(runId, onEvent) {
  const source = new EventSource(`${BASE}/api/stream/${runId}`);
  source.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    onEvent(data);
    if (data.type === "done") source.close();
  };
  source.onerror = () => source.close();
  return () => source.close();
}

export async function fetchHitl() {
  return json(await fetch(`${BASE}/api/hitl`));
}

export async function openHitl(entryId) {
  return json(await fetch(`${BASE}/api/hitl/${entryId}/open`, { method: "POST" }));
}

export async function resolveHitl(entryId) {
  return json(await fetch(`${BASE}/api/hitl/${entryId}/resolve`, { method: "POST" }));
}

export async function fetchStats(profile) {
  const qs = profile ? `?profile=${encodeURIComponent(profile)}` : "";
  return json(await fetch(`${BASE}/api/stats${qs}`));
}

export async function fetchApplications({ profile, status, q } = {}) {
  const params = new URLSearchParams();
  if (profile) params.set("profile", profile);
  if (status) params.set("status", status);
  if (q) params.set("q", q);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return json(await fetch(`${BASE}/api/applications${qs}`));
}
