import { useEffect, useMemo, useState } from "react";
import { LineChart } from "lucide-react";
import { fetchApplications } from "../api";
import { PLATFORM_LABELS } from "../platforms";

const STATUS_COLOR = {
  applied: "bg-sage",
  needs_review: "bg-amber-500",
  pending_review: "bg-blue-500",
  failed: "bg-red-500",
  skipped: "bg-ink/20",
  duplicate: "bg-ink/10",
};

function Bar({ label, count, total, colorClass }) {
  const pct = total ? Math.round((count / total) * 100) : 0;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs text-ink/60">
        <span className="font-medium text-ink">{label}</span>
        <span>
          {count} · {pct}%
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-line">
        <div className={`h-full ${colorClass}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function TrackerTab({ profile }) {
  const [apps, setApps] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchApplications({ profile })
      .then(setApps)
      .catch(() => setApps([]))
      .finally(() => setLoading(false));
  }, [profile]);

  const byPlatform = useMemo(() => {
    const map = {};
    for (const a of apps) {
      map[a.platform] = map[a.platform] || { total: 0, applied: 0 };
      map[a.platform].total++;
      if (a.status === "applied") map[a.platform].applied++;
    }
    return map;
  }, [apps]);

  const byStatus = useMemo(() => {
    const map = {};
    for (const a of apps) {
      map[a.status] = (map[a.status] || 0) + 1;
    }
    return map;
  }, [apps]);

  const total = apps.length;

  return (
    <div className="mx-auto max-w-md px-4 pb-10 pt-6 sm:max-w-2xl sm:px-6 lg:max-w-none lg:px-10 xl:px-16">
      <div className="mb-4 flex items-center gap-2">
        <LineChart size={20} className="text-sage" />
        <h1 className="text-lg font-bold text-ink">Tracker</h1>
      </div>

      {loading && <div className="py-10 text-center text-sm text-ink/40">Loading…</div>}

      {!loading && total === 0 && (
        <div className="rounded-3xl border border-dashed border-line bg-white/60 py-14 text-center text-sm text-ink/40">
          Run your agent to start seeing breakdowns here.
        </div>
      )}

      {!loading && total > 0 && (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <div className="rounded-3xl border border-line bg-white p-4">
            <div className="mb-3 text-xs font-bold uppercase tracking-wide text-ink/40">By outcome</div>
            <div className="flex flex-col gap-3">
              {Object.entries(byStatus)
                .sort((a, b) => b[1] - a[1])
                .map(([status, count]) => (
                  <Bar key={status} label={status.replace("_", " ")} count={count} total={total} colorClass={STATUS_COLOR[status] || "bg-ink/30"} />
                ))}
            </div>
          </div>

          <div className="rounded-3xl border border-line bg-white p-4">
            <div className="mb-3 text-xs font-bold uppercase tracking-wide text-ink/40">By platform</div>
            <div className="flex flex-col gap-3">
              {Object.entries(byPlatform)
                .sort((a, b) => b[1].total - a[1].total)
                .map(([platform, s]) => (
                  <Bar key={platform} label={PLATFORM_LABELS[platform] || platform} count={s.total} total={total} colorClass="bg-sun" />
                ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
