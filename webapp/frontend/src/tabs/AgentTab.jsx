import { useRef, useState } from "react";
import { Bot, FileUp, Plus, Sparkles, Square, X } from "lucide-react";
import { ResultCard, PlatformCard } from "../components/PersonRun";
import StepTracker from "../components/StepTracker";
import { stopRun } from "../api";

let rowSeq = 0;
function newRow() {
  rowSeq += 1;
  return { id: rowSeq, file: null, profile: "" };
}

function PersonSectionView({
  label,
  forLabel,
  step,
  profileInfo,
  searchTitles,
  platforms,
  results,
  banner,
  onStop,
  stopping,
  onCancelConflictAndRetry,
  cancelling,
}) {
  const canStop = !banner && step < 4;
  return (
    <div className="rounded-3xl border border-line bg-white/60 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-bold text-ink">{label}</span>
          <span className="text-xs text-ink/40">· {forLabel}</span>
        </div>
        {canStop && (
          <button
            type="button"
            onClick={onStop}
            disabled={stopping}
            className="flex shrink-0 items-center gap-1.5 rounded-full border border-red-200 bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-600 disabled:opacity-50"
          >
            <Square size={11} className="fill-current" />
            {stopping ? "Stopping…" : "Stop"}
          </button>
        )}
      </div>
      <StepTracker step={step} />

      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-[1fr_360px] lg:items-start">
        <div>
          {profileInfo && (
            <div className="rounded-2xl border border-line bg-sage-light/60 p-3 text-xs text-ink/70">
              <span className="font-semibold text-ink">{profileInfo.name}</span> · {profileInfo.headline} · {profileInfo.years_of_experience} yrs experience
              <div className="mt-1.5 flex flex-wrap gap-1">
                {(profileInfo.skills || []).map((s) => (
                  <span key={s} className="rounded-full border border-line bg-white px-2 py-0.5 text-[11px] text-ink/60">
                    {s}
                  </span>
                ))}
              </div>
              {searchTitles && <div className="mt-2 text-[11px] text-ink/50">Searching for: {searchTitles.join(", ")}</div>}
            </div>
          )}
          {Object.keys(platforms).length > 0 && (
            <div className="mt-3 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
              {Object.values(platforms).map((s) => (
                <PlatformCard key={s.platform} state={s} />
              ))}
            </div>
          )}
          {banner && (
            <div className={`mt-3 rounded-2xl border p-3 text-xs ${banner.kind === "error" ? "border-red-300 bg-red-50 text-red-700" : "border-sage/40 bg-sage-light text-sage"}`}>
              {banner.text}
              {banner.conflictRunId !== undefined && banner.conflictRunId !== null && (
                <button
                  type="button"
                  onClick={() => onCancelConflictAndRetry(banner.conflictRunId)}
                  disabled={cancelling}
                  className="mt-2 flex items-center gap-1.5 rounded-full border border-red-300 bg-white px-3 py-1.5 text-xs font-semibold text-red-600 disabled:opacity-50"
                >
                  <Square size={11} className="fill-current" />
                  {cancelling ? "Cancelling…" : "Cancel that run & retry"}
                </button>
              )}
            </div>
          )}
        </div>

        <div className="lg:sticky lg:top-4">
          <div className="mb-2 text-xs font-bold uppercase tracking-wide text-ink/40">
            Results {results.length > 0 && `(${results.length})`}
          </div>
          {results.length > 0 ? (
            <div className="flex flex-col gap-2 lg:max-h-[calc(100vh-9rem)] lg:overflow-y-auto lg:pr-1">
              {results.map((r, i) => (
                <ResultCard key={i} evt={r} />
              ))}
            </div>
          ) : (
            <div className="rounded-2xl border border-dashed border-line p-5 text-center text-xs text-ink/35">
              Results will show up here as postings are scored, applied to, or sent to your review queue.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FileRow({ row, onChange, onRemove, removable }) {
  const inputRef = useRef(null);
  return (
    <div className="flex items-center gap-2 rounded-2xl border border-line bg-white p-2 pl-3">
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        className={`flex flex-1 items-center gap-2 truncate text-left text-sm ${row.file ? "text-sage font-medium" : "text-ink/40"}`}
      >
        <FileUp size={16} className="shrink-0" />
        <span className="truncate">{row.file ? row.file.name : "Click to choose a resume (PDF / DOCX / TXT)"}</span>
      </button>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.docx,.txt"
        className="hidden"
        onChange={(e) => onChange({ ...row, file: e.target.files[0] || null })}
      />
      <input
        type="text"
        placeholder="Account (blank = you)"
        value={row.profile}
        onChange={(e) => onChange({ ...row, profile: e.target.value })}
        className="w-28 shrink-0 rounded-xl border border-line bg-cream px-2.5 py-1.5 text-xs text-ink outline-none placeholder:text-ink/40 sm:w-40"
      />
      {removable && (
        <button type="button" onClick={onRemove} className="shrink-0 text-ink/30 hover:text-red-500">
          <X size={16} />
        </button>
      )}
    </div>
  );
}

export default function AgentTab() {
  const [rows, setRows] = useState([newRow()]);
  const [minFitScore, setMinFitScore] = useState(0.6);
  const [started, setStarted] = useState(false);
  const [sections, setSections] = useState([]);
  const [runIds, setRunIds] = useState({});
  const sectionRefs = useRef({});

  const ready = rows.some((r) => r.file);

  function updateRow(id, next) {
    setRows((prev) => prev.map((r) => (r.id === id ? next : r)));
  }
  function removeRow(id) {
    setRows((prev) => prev.filter((r) => r.id !== id));
  }

  async function start() {
    const jobs = rows.filter((r) => r.file);
    if (!jobs.length) return;
    setStarted(true);

    const newSections = jobs.map((job) => {
      const label = job.profile ? job.profile : job.file.name.replace(/\.[^.]+$/, "");
      const forLabel = job.profile ? `account: ${job.profile}` : "your account";
      return { key: `${job.id}`, label, forLabel, job };
    });
    setSections(newSections);

    for (const sec of newSections) {
      runOne(sec);
    }
  }

  async function runOne(sec) {
    const form = new FormData();
    form.append("resume", sec.job.file);
    if (sec.job.profile) form.append("profile", sec.job.profile);
    form.append("min_fit_score", String(minFitScore));

    try {
      const res = await fetch("/api/run", { method: "POST", body: form });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = body.detail;
        if (res.status === 409 && detail && typeof detail === "object") {
          sectionRefs.current[sec.key]?.({
            type: "error",
            message: detail.message || "A run is already in progress for this account.",
            conflictRunId: detail.run_id || null,
          });
          return;
        }
        throw new Error((typeof detail === "string" && detail) || `Could not start this run (HTTP ${res.status})`);
      }
      const { run_id } = await res.json();
      setRunIds((prev) => ({ ...prev, [sec.key]: run_id }));
      const es = new EventSource(`/api/stream/${run_id}`);
      es.onmessage = (ev) => {
        const evt = JSON.parse(ev.data);
        sectionRefs.current[sec.key]?.(evt);
        if (evt.type === "done" || evt.type === "error") es.close();
      };
      es.onerror = () => es.close();
    } catch (e) {
      sectionRefs.current[sec.key]?.({ type: "error", message: String(e.message || e) });
    }
  }

  async function cancelConflictAndRetry(sec, conflictRunId) {
    try {
      if (conflictRunId) await stopRun(conflictRunId);
    } catch {
      // the run may have already finished on its own -- fine either way, retry below
    }
    sectionRefs.current[sec.key]?.({ type: "retrying" });
    runOne(sec);
  }

  return (
    <div className="mx-auto max-w-md px-4 pb-10 pt-6 sm:max-w-2xl sm:px-6 lg:max-w-none lg:px-10 xl:px-16">
      {!started ? (
        <div className="relative -mx-4 -mt-6 -mb-10 flex min-h-screen flex-col items-center justify-center overflow-hidden bg-gradient-to-b from-sage-light/70 via-cream to-cream px-5 py-16 sm:-mx-6 sm:py-20 lg:-mx-10 xl:-mx-16">
          <div className="pointer-events-none absolute -left-16 -top-24 h-72 w-72 rounded-full bg-sun/30 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-24 -right-10 h-72 w-72 rounded-full bg-sage/25 blur-3xl" />

          <div className="relative mx-auto max-w-2xl text-center">
            <div className="mx-auto mb-5 flex w-fit items-center gap-1.5 rounded-full border border-line bg-white/80 px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-sage">
              <Sparkles size={12} /> AI job agent
            </div>
            <h1 className="text-4xl font-bold leading-[1.1] text-ink sm:text-5xl">
              Be the first <span className="italic text-sage">to every job</span>
            </h1>
            <p className="mx-auto mt-4 max-w-md text-sm leading-relaxed text-ink/55">
              Drop a resume and I'll parse it, score every open role on LinkedIn &amp; Wellfound, then apply to the
              ones that fit.
            </p>
          </div>

          <div className="relative mx-auto mt-8 max-w-2xl rounded-3xl border border-line bg-white p-4 shadow-sm">
            <div className="flex flex-col gap-2">
              {rows.map((row) => (
                <FileRow key={row.id} row={row} onChange={(next) => updateRow(row.id, next)} onRemove={() => removeRow(row.id)} removable={rows.length > 1} />
              ))}
            </div>
            <button
              type="button"
              onClick={() => setRows((prev) => [...prev, newRow()])}
              className="mt-2 flex items-center gap-1.5 rounded-xl border border-dashed border-line px-3 py-1.5 text-xs font-medium text-ink/50"
            >
              <Plus size={14} /> Add another person
            </button>

            <div className="mt-4 flex items-center gap-2">
              <label htmlFor="min-fit" className="text-xs font-semibold text-ink">
                Minimum fit score
              </label>
              <input
                id="min-fit"
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={minFitScore}
                onChange={(e) => setMinFitScore(Number(e.target.value))}
                className="w-20 rounded-xl border border-line bg-cream px-2 py-1 text-xs text-ink"
              />
            </div>
            <p className="mt-1.5 text-[11px] text-ink/40">0.0-1.0 -- postings scored below this are skipped, not applied to.</p>

            <button
              type="button"
              disabled={!ready}
              onClick={start}
              className={`mt-4 w-full rounded-full py-3.5 text-sm font-semibold transition-colors ${
                ready ? "bg-ink text-cream" : "cursor-not-allowed bg-ink/20 text-white/70"
              }`}
            >
              Watch it apply
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-5">
          <div className="mx-auto flex w-full max-w-2xl items-center gap-3 lg:max-w-none">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-sage text-white">
              <Bot size={22} />
            </div>
            <div>
              <div className="text-lg font-bold text-ink">
                Your <span className="text-sage">agent</span>
              </div>
              <div className="text-xs text-ink/50">Watches jobs, tailors your resume, applies the moment one fits.</div>
            </div>
          </div>
          {sections.map((sec) => (
            <TrackedSection
              key={sec.key}
              sec={sec}
              sectionRefs={sectionRefs}
              runId={runIds[sec.key]}
              onCancelConflictAndRetry={(conflictRunId) => cancelConflictAndRetry(sec, conflictRunId)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function TrackedSection({ sec, sectionRefs, runId, onCancelConflictAndRetry }) {
  const bindRef = (fn) => {
    sectionRefs.current[sec.key] = fn;
  };
  return (
    <PersonSectionBound
      label={sec.label}
      forLabel={sec.forLabel}
      bindRef={bindRef}
      runId={runId}
      onCancelConflictAndRetry={onCancelConflictAndRetry}
    />
  );
}

function PersonSectionBound({ label, forLabel, bindRef, runId, onCancelConflictAndRetry }) {
  const [step, setStep] = useState(0);
  const [profileInfo, setProfileInfo] = useState(null);
  const [searchTitles, setSearchTitles] = useState(null);
  const [platforms, setPlatforms] = useState({});
  const [results, setResults] = useState([]);
  const [banner, setBanner] = useState(null);
  const [stopping, setStopping] = useState(false);

  async function handleStop() {
    if (!runId || stopping) return;
    setStopping(true);
    try {
      await stopRun(runId);
    } catch (e) {
      setBanner({ kind: "error", text: "Could not stop: " + e.message });
      setStopping(false);
    }
  }

  function ensurePlatform(platform) {
    setPlatforms((prev) => {
      if (prev[platform]) return prev;
      return { ...prev, [platform]: { platform, status: "starting…", applied: 0, reviewed: 0, failed: 0, skipped: 0, duplicate: 0, error: "", done: false } };
    });
  }
  function statusLine(s) {
    let text = `applied ${s.applied} · needs review ${s.reviewed} · skipped ${s.skipped} · failed ${s.failed}`;
    if (s.duplicate) text += ` · ${s.duplicate} already processed earlier`;
    return text;
  }

  const [cancelling, setCancelling] = useState(false);

  async function handleCancelConflictAndRetry(conflictRunId) {
    setCancelling(true);
    try {
      await onCancelConflictAndRetry(conflictRunId);
    } finally {
      setCancelling(false);
    }
  }

  bindRef((evt) => {
    switch (evt.type) {
      case "step":
        setBanner(null);
        setStep(evt.step);
        break;
      case "retrying":
        setBanner({ kind: "ok", text: "Retrying…" });
        break;
      case "profile":
        setProfileInfo(evt);
        break;
      case "search_titles":
        setSearchTitles(evt.titles);
        break;
      case "platform_start":
        ensurePlatform(evt.platform);
        break;
      case "platform_search":
        ensurePlatform(evt.platform);
        setPlatforms((prev) => ({ ...prev, [evt.platform]: { ...prev[evt.platform], status: `found ${evt.count} candidate postings — applying…` } }));
        break;
      case "platform_error":
        ensurePlatform(evt.platform);
        setPlatforms((prev) => ({ ...prev, [evt.platform]: { ...prev[evt.platform], error: evt.message } }));
        break;
      case "platform_skipped":
        ensurePlatform(evt.platform);
        setPlatforms((prev) => ({ ...prev, [evt.platform]: { ...prev[evt.platform], status: evt.message, done: true } }));
        break;
      case "platform_done":
        setPlatforms((prev) => {
          const s = prev[evt.platform];
          if (!s) return prev;
          return { ...prev, [evt.platform]: { ...s, done: true, status: s.error ? s.status : statusLine(s) } };
        });
        break;
      case "result":
        ensurePlatform(evt.platform);
        setPlatforms((prev) => {
          const s = prev[evt.platform] || { platform: evt.platform, applied: 0, reviewed: 0, failed: 0, skipped: 0, duplicate: 0, status: "", error: "", done: false };
          const next = { ...s };
          if (evt.status === "applied") next.applied++;
          else if (evt.status === "needs_review") next.reviewed++;
          else if (evt.status === "failed") next.failed++;
          else if (evt.status === "duplicate") next.duplicate++;
          else next.skipped++;
          next.status = statusLine(next);
          next.currentJob = { title: evt.title, company: evt.company, status: evt.status };
          return { ...prev, [evt.platform]: next };
        });
        if (evt.status !== "skipped" && evt.status !== "duplicate") {
          setResults((prev) => [evt, ...prev]);
        }
        break;
      case "error":
        setBanner({ kind: "error", text: "Error: " + evt.message, conflictRunId: evt.conflictRunId || null });
        break;
      case "stopped":
        setBanner({ kind: "ok", text: "Stopped by you." });
        setStep(4);
        break;
      case "done":
        setStep(4);
        setBanner((prev) => prev || { kind: "ok", text: "Run complete." });
        break;
      default:
        break;
    }
  });

  return (
    <PersonSectionView
      label={label}
      forLabel={forLabel}
      step={step}
      profileInfo={profileInfo}
      searchTitles={searchTitles}
      platforms={platforms}
      results={results}
      banner={banner}
      onStop={handleStop}
      stopping={stopping}
      onCancelConflictAndRetry={handleCancelConflictAndRetry}
      cancelling={cancelling}
    />
  );
}
