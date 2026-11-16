import { useState } from "react";
import { CheckCircle2, ChevronDown, Copy, Eye, Loader2, SkipForward, XCircle } from "lucide-react";
import { PLATFORM_LABELS } from "../platforms";

const PLATFORM_COLORS = {
  linkedin: "bg-blue-600",
  wellfound: "bg-black",
  ashby: "bg-sage",
  workable: "bg-sun",
  rippling: "bg-violet-600",
};

const BADGE_STYLES = {
  applied: "bg-sage-light text-sage",
  needs_review: "bg-amber-100 text-amber-700",
  pending_review: "bg-blue-100 text-blue-700",
  failed: "bg-red-100 text-red-600",
};

function PlatformMonogram({ platform, size = 32 }) {
  const label = PLATFORM_LABELS[platform] || platform;
  return (
    <div
      className={`flex shrink-0 items-center justify-center rounded-full text-xs font-bold text-white ${PLATFORM_COLORS[platform] || "bg-ink/40"}`}
      style={{ width: size, height: size }}
    >
      {label.slice(0, 2).toUpperCase()}
    </div>
  );
}

const BULLET_RE = /^[-*•·‣◦]\s+(.*)/;
const NUMBERED_RE = /^\d+[.)]\s+(.*)/;

// Scraped job descriptions are plain text with no blank-line paragraph
// breaks to key off, so splitting only on blank lines (the old approach)
// collapsed most real postings into one dense wall of text. This instead
// walks line by line, grouping consecutive bullet/numbered lines into a
// <ul> and consecutive plain lines into a <p> (reflowed, not pre-wrapped),
// breaking into a new block on a blank line or a type change.
function FormattedJD({ text }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let current = null;

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      current = null;
      continue;
    }
    const bulletMatch = line.match(BULLET_RE) || line.match(NUMBERED_RE);
    if (bulletMatch) {
      if (!current || current.type !== "ul") {
        current = { type: "ul", items: [] };
        blocks.push(current);
      }
      current.items.push(bulletMatch[1]);
    } else {
      if (!current || current.type !== "p") {
        current = { type: "p", items: [] };
        blocks.push(current);
      }
      current.items.push(line);
    }
  }

  return (
    <div className="space-y-3 text-xs leading-relaxed text-ink/70">
      {blocks.map((b, i) =>
        b.type === "ul" ? (
          <ul key={i} className="list-disc space-y-1 pl-4 marker:text-sage">
            {b.items.map((item, j) => (
              <li key={j}>{item}</li>
            ))}
          </ul>
        ) : (
          <p key={i}>{b.items.join(" ")}</p>
        )
      )}
    </div>
  );
}

function ResultCard({ evt }) {
  const [jdOpen, setJdOpen] = useState(false);
  const fitPct = Math.round((evt.fit || 0) * 100);
  return (
    <div className="rounded-2xl border border-line bg-white p-3.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <PlatformMonogram platform={evt.platform} size={28} />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-ink">{evt.title}</div>
            <div className="text-xs text-ink/50">
              {evt.company} · {PLATFORM_LABELS[evt.platform] || evt.platform}
            </div>
          </div>
        </div>
        <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${BADGE_STYLES[evt.status] || "bg-ink/10 text-ink/60"}`}>
          {evt.status.replace("_", " ")}
        </span>
      </div>
      <div className="mt-2 text-xs text-ink/50">
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
        <div className="mt-2 border-t border-line pt-2">
          <button
            onClick={() => setJdOpen((v) => !v)}
            className="flex items-center gap-1 text-xs font-medium text-sage"
          >
            <ChevronDown size={14} className={`transition-transform ${jdOpen ? "rotate-180" : ""}`} />
            {jdOpen ? "Hide job description" : "Show job description"}
          </button>
          {jdOpen && (
            <div className="mt-2 rounded-xl bg-cream/70 p-3">
              <FormattedJD text={evt.jd} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatChip({ icon: Icon, count, label, colorClass }) {
  if (!count) return null;
  return (
    <div className={`flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold ${colorClass}`} title={label}>
      <Icon size={12} />
      {count}
    </div>
  );
}

function PlatformCard({ state }) {
  const hasStarted = state.applied + state.reviewed + state.failed + state.skipped + state.duplicate > 0;
  return (
    <div className="rounded-2xl border border-line bg-white p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <PlatformMonogram platform={state.platform} />
          <div>
            <h3 className="text-sm font-bold text-ink">{PLATFORM_LABELS[state.platform] || state.platform}</h3>
            <div className="text-[11px] text-ink/40">{state.done ? "finished" : "in progress…"}</div>
          </div>
        </div>
        {!state.done && <Loader2 size={16} className="shrink-0 animate-spin text-sage" />}
        {state.done && !state.error && <CheckCircle2 size={16} className="shrink-0 text-sage" />}
      </div>

      <div className="mt-3 text-xs text-ink/55">{state.status}</div>

      {state.currentJob && (
        <div
          key={`${state.currentJob.title}-${state.currentJob.company}`}
          className="job-ticker mt-2.5 truncate rounded-xl bg-cream px-2.5 py-1.5 text-[11px] text-ink/60"
        >
          <span className="font-medium text-ink/70">{state.done ? "Last checked:" : "Now checking:"}</span>{" "}
          {state.currentJob.title}
          {state.currentJob.company && <span className="text-ink/40"> · {state.currentJob.company}</span>}
        </div>
      )}

      {hasStarted && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          <StatChip icon={CheckCircle2} count={state.applied} label="applied" colorClass="bg-sage-light text-sage" />
          <StatChip icon={Eye} count={state.reviewed} label="needs review" colorClass="bg-amber-100 text-amber-700" />
          <StatChip icon={SkipForward} count={state.skipped} label="skipped" colorClass="bg-ink/10 text-ink/50" />
          <StatChip icon={XCircle} count={state.failed} label="failed" colorClass="bg-red-100 text-red-600" />
          <StatChip icon={Copy} count={state.duplicate} label="already processed earlier" colorClass="bg-blue-100 text-blue-700" />
        </div>
      )}

      {state.error && <div className="mt-2.5 rounded-xl bg-red-50 p-2 text-xs text-red-600">{state.error}</div>}
    </div>
  );
}

export { PlatformMonogram, ResultCard, PlatformCard };
