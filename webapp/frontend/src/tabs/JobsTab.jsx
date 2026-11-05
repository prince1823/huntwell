import { useEffect, useState } from "react";
import { Briefcase, ExternalLink, Loader2 } from "lucide-react";
import { fetchHitl, openHitl, resolveHitl } from "../api";

const PLATFORM_LABELS = { linkedin: "LinkedIn", wellfound: "Wellfound", ashby: "Ashby", workable: "Workable", rippling: "Rippling" };

const STATUS_META = {
  pending: { label: "Filled and ready", dot: "bg-sun" },
  opening: { label: "Opening the browser…", dot: "bg-amber-500" },
  opened: { label: "Open on your screen -- solve &amp; submit there", dot: "bg-sage" },
  failed: { label: "Something went wrong -- check the window if one opened", dot: "bg-red-500" },
};

function JobCard({ item, onOpen, onResolve, busy }) {
  const meta = STATUS_META[item.status] || STATUS_META.pending;
  return (
    <div className="rounded-3xl border border-line bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-bold text-ink">{item.title}</div>
          <div className="mt-0.5 text-xs text-ink/50">
            {item.company} · {PLATFORM_LABELS[item.platform] || item.platform}
          </div>
        </div>
        {item.url && (
          <a href={item.url} target="_blank" rel="noreferrer" className="shrink-0 text-ink/30">
            <ExternalLink size={16} />
          </a>
        )}
      </div>
      <div className="mt-2 flex items-center gap-1.5 text-xs text-ink/50">
        <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
        {item.status === "opened" ? "Open on your screen — solve & submit there" : meta.label}
      </div>
      <div className="mt-3 flex gap-2">
        <button
          onClick={() => onOpen(item.id)}
          disabled={busy || item.status === "opening" || item.status === "opened"}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-full bg-ink py-2 text-xs font-semibold text-cream disabled:opacity-40"
        >
          {item.status === "opening" && <Loader2 size={13} className="animate-spin" />}
          {item.status === "opened" ? "Opened" : item.status === "opening" ? "Opening…" : item.status === "failed" ? "Retry open" : "Open & review"}
        </button>
        <button onClick={() => onResolve(item.id)} className="rounded-full border border-line px-4 py-2 text-xs font-semibold text-ink/60">
          Mark done
        </button>
      </div>
    </div>
  );
}

export default function JobsTab() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const data = await fetchHitl();
        if (!cancelled) setItems(data);
      } catch {
        // next poll reconciles
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    poll();
    const id = setInterval(poll, 4000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  async function handleOpen(id) {
    setBusyId(id);
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, status: "opening" } : it)));
    try {
      await openHitl(id);
    } finally {
      setBusyId(null);
    }
  }

  async function handleResolve(id) {
    setItems((prev) => prev.filter((it) => it.id !== id));
    try {
      await resolveHitl(id);
    } catch {
      // ignore
    }
  }

  return (
    <div className="mx-auto max-w-md px-4 pb-10 pt-6 sm:max-w-2xl sm:px-6 lg:max-w-none lg:px-10 xl:px-16">
      <div className="mb-1 flex items-center gap-2">
        <Briefcase size={20} className="text-sage" />
        <h1 className="text-lg font-bold text-ink">Review &amp; submit</h1>
      </div>
      <p className="mb-4 text-xs text-ink/50">
        Filled completely and held right before Submit. Open to bring up the real browser window, solve any captcha, and
        submit yourself.
      </p>

      {loading && <div className="py-12 text-center text-sm text-ink/40">Loading…</div>}

      {!loading && items.length === 0 && (
        <div className="rounded-3xl border border-dashed border-line bg-white/60 py-14 text-center">
          <Briefcase size={28} className="mx-auto mb-2 text-ink/20" />
          <div className="text-sm font-medium text-ink/50">Nothing waiting on you right now</div>
          <div className="mt-1 text-xs text-ink/35">Jobs your agent fills on captcha-protected sites will show up here.</div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {items.map((item) => (
          <JobCard key={item.id} item={item} onOpen={handleOpen} onResolve={handleResolve} busy={busyId === item.id} />
        ))}
      </div>
    </div>
  );
}
