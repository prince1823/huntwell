import { useEffect, useState } from "react";
import { CheckCircle2, Clock, Search, SendHorizonal, UserCheck } from "lucide-react";
import { fetchApplications, fetchStats } from "../api";
import { PLATFORM_LABELS } from "../platforms";

const STATUS_FILTERS = [
  { id: null, label: "All" },
  { id: "applied", label: "Applied" },
  { id: "needs_review", label: "Needs review" },
  { id: "pending_review", label: "Pending" },
  { id: "failed", label: "Failed" },
];

const STATUS_DOT = {
  applied: "bg-sage",
  needs_review: "bg-amber-500",
  pending_review: "bg-blue-500",
  failed: "bg-red-500",
  skipped: "bg-ink/20",
};

function StatTile({ icon: Icon, label, value }) {
  return (
    <div className="rounded-2xl border border-line bg-white p-3">
      <Icon size={16} className="text-sage" />
      <div className="mt-2 text-xl font-bold text-ink">{value}</div>
      <div className="text-[11px] text-ink/45">{label}</div>
    </div>
  );
}

function formatDate(s) {
  if (!s) return "";
  try {
    return new Date(s).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return s;
  }
}

export default function AppliedTab({ profile }) {
  const [stats, setStats] = useState(null);
  const [apps, setApps] = useState([]);
  const [status, setStatus] = useState(null);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchStats(profile).then(setStats).catch(() => setStats(null));
  }, [profile]);

  useEffect(() => {
    setLoading(true);
    const handle = setTimeout(() => {
      fetchApplications({ profile, status, q: q || undefined })
        .then(setApps)
        .catch(() => setApps([]))
        .finally(() => setLoading(false));
    }, 250);
    return () => clearTimeout(handle);
  }, [profile, status, q]);

  return (
    <div className="mx-auto max-w-md px-4 pb-10 pt-6 sm:max-w-2xl sm:px-6 lg:max-w-none lg:px-10 xl:px-16">
      <h1 className="mb-4 text-lg font-bold text-ink">Applied</h1>

      {stats && (
        <div className="mb-4 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <StatTile icon={SendHorizonal} label="Submitted" value={stats.submitted} />
          <StatTile icon={CheckCircle2} label="Successful" value={stats.successful} />
          <StatTile icon={UserCheck} label="Needs you" value={stats.needs_you} />
          <StatTile icon={Clock} label="Minutes saved" value={stats.time_saved_minutes} />
        </div>
      )}

      <div className="mb-3 flex items-center gap-2 rounded-full border border-line bg-white px-3.5 py-2">
        <Search size={15} className="text-ink/30" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search company or title"
          className="w-full bg-transparent text-sm outline-none placeholder:text-ink/30"
        />
      </div>

      <div className="mb-4 flex gap-1.5 overflow-x-auto">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.label}
            onClick={() => setStatus(f.id)}
            className={`shrink-0 rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${
              status === f.id ? "bg-ink text-cream" : "bg-white text-ink/50 border border-line"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {loading && <div className="py-10 text-center text-sm text-ink/40">Loading…</div>}

      {!loading && apps.length === 0 && (
        <div className="rounded-3xl border border-dashed border-line bg-white/60 py-14 text-center text-sm text-ink/40">
          No applications yet{status ? " for this filter" : ""}.
        </div>
      )}

      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {apps.map((a) => (
          <div key={`${a.platform}-${a.job_id}`} className="rounded-2xl border border-line bg-white p-3.5">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-ink">{a.title || "Untitled role"}</div>
                <div className="text-xs text-ink/50">
                  {a.company} · {PLATFORM_LABELS[a.platform] || a.platform}
                </div>
              </div>
              <span className="shrink-0 text-[11px] text-ink/35">{formatDate(a.applied_at)}</span>
            </div>
            <div className="mt-1.5 flex items-center gap-1.5 text-xs text-ink/50">
              <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[a.status] || "bg-ink/20"}`} />
              {(a.status || "").replace("_", " ")}
              {a.url && (
                <a href={a.url} target="_blank" rel="noreferrer" className="ml-auto text-sage underline">
                  view
                </a>
              )}
            </div>
            {a.reason && <div className="mt-1 text-[11px] text-amber-700">{a.reason}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
