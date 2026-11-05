import { useState } from "react";
import { Loader2 } from "lucide-react";

const PLATFORM_LABELS = { linkedin: "LinkedIn", wellfound: "Wellfound", ashby: "Ashby", workable: "Workable", rippling: "Rippling" };

const BADGE_STYLES = {
  applied: "bg-sage-light text-sage",
  needs_review: "bg-amber-100 text-amber-700",
  pending_review: "bg-blue-100 text-blue-700",
  failed: "bg-red-100 text-red-600",
};

function ResultCard({ evt }) {
  const [jdOpen, setJdOpen] = useState(false);
  const fitPct = Math.round((evt.fit || 0) * 100);
  return (
    <div className="rounded-2xl border border-line bg-white p-3.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-ink">{evt.title}</div>
          <div className="text-xs text-ink/50">
            {evt.company} · {PLATFORM_LABELS[evt.platform] || evt.platform}
          </div>
        </div>
        <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${BADGE_STYLES[evt.status] || "bg-ink/10 text-ink/60"}`}>
          {evt.status.replace("_", " ")}
        </span>
      </div>
      <div className="mt-1.5 text-xs text-ink/50">
        fit score {fitPct}%
        {evt.url && (
          <>
            {" · "}
            <a href={evt.url} target="_blank" rel="noreferrer" className="text-sage underline">
              view posting
            </a>
          </>
        )}
      </div>
      {evt.reason && <div className="mt-1 text-xs text-amber-700">{evt.reason}</div>}
      {evt.jd && (
        <div className="mt-1.5">
          <button onClick={() => setJdOpen((v) => !v)} className="text-xs font-medium text-sage">
            {jdOpen ? "Hide job description" : "Show job description"}
          </button>
          {jdOpen && <div className="mt-1.5 max-h-40 overflow-y-auto whitespace-pre-wrap border-t border-line pt-1.5 text-xs text-ink/50">{evt.jd}</div>}
        </div>
      )}
    </div>
  );
}

function PlatformCard({ state }) {
  return (
    <div className="rounded-2xl border border-line bg-white p-3.5">
      <h3 className="flex items-center justify-between text-sm font-semibold text-ink">
        {PLATFORM_LABELS[state.platform] || state.platform}
        {!state.done && <Loader2 size={14} className="animate-spin text-sage" />}
      </h3>
      <div className="mt-1 text-xs text-ink/50">{state.status}</div>
      {state.error && <div className="mt-1 text-xs text-red-600">{state.error}</div>}
    </div>
  );
}

export { PLATFORM_LABELS, ResultCard, PlatformCard };
