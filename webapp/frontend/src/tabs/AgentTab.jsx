import { useRef, useState } from "react";
import { Bot, FileUp, Plus, Sparkles, X } from "lucide-react";
import { ResultCard, PlatformCard } from "../components/PersonRun";
import StepTracker from "../components/StepTracker";

let rowSeq = 0;
function newRow() {
  rowSeq += 1;
  return { id: rowSeq, file: null, profile: "" };
}

function PersonSectionView({ label, forLabel, step, profileInfo, searchTitles, platforms, results, banner }) {
  return (
    <div className="rounded-3xl border border-line bg-white/60 p-4">
      <div className="mb-3 flex items-baseline gap-2">
        <span className="text-sm font-bold text-ink">{label}</span>
        <span className="text-xs text-ink/40">· {forLabel}</span>
      </div>
      <StepTracker step={step} />
      {profileInfo && (
        <div className="mt-3 rounded-2xl border border-line bg-sage-light/60 p-3 text-xs text-ink/70">
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
      {results.length > 0 && (
        <div className="mt-3 flex flex-col gap-2">
          {results.map((r, i) => (
            <ResultCard key={i} evt={r} />
          ))}
        </div>
      )}
      {banner && (
        <div className={`mt-3 rounded-2xl border p-3 text-xs ${banner.kind === "error" ? "border-red-300 bg-red-50 text-red-700" : "border-sage/40 bg-sage-light text-sage"}`}>
          {banner.text}
        </div>
      )}
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
        throw new Error(body.detail || `Could not start this run (HTTP ${res.status})`);
      }
      const { run_id } = await res.json();
      const es = new EventSource(`/api/stream/${run_id}`);
      es.onmessage = (ev) => {
        const evt = JSON.parse(ev.data);
        sectionRefs.current[sec.key]?.(evt);
        if (evt.type === "done" || evt.type === "error") es.close();
      };
      es.onerror = () => es.close();
    } catch (e) {
      sectionRefs.current[sec.key]?.({ type: "error", message: String(e) });
    }
  }

  return (
    <div className="mx-auto max-w-md px-4 pb-28 pt-6">
      <div className="mb-5 flex items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-full bg-sage text-white">
          <Bot size={22} />
        </div>
        <div>
          <div className="text-lg font-bold text-ink">
            Your <span className="text-sage">agent</span>
          </div>
          <div className="text-xs text-ink/50">Watches jobs, tailors your resume, applies the moment one fits.</div>
        </div>
      </div>

      <div className="mb-4 rounded-3xl bg-ink px-4 py-3.5 text-sm leading-relaxed text-cream">
        <Sparkles size={14} className="mb-1 inline text-sun" /> Drop a resume below and I'll parse it, score every open
        role on LinkedIn, Wellfound, Ashby, Workable &amp; Rippling, then apply to the ones that fit -- handing anything
        with a captcha to your Jobs queue for a quick human click.
      </div>

      {!started && (
        <div className="rounded-3xl border border-line bg-white p-4">
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
            className={`mt-4 w-full rounded-full py-3 text-sm font-semibold transition-colors ${
              ready ? "bg-ink text-cream" : "cursor-not-allowed bg-ink/20 text-white/70"
            }`}
          >
            Watch it apply
          </button>
        </div>
      )}

      {started && (
        <div className="flex flex-col gap-5">
          {sections.map((sec) => (
            <TrackedSection key={sec.key} sec={sec} sectionRefs={sectionRefs} />
          ))}
        </div>
      )}
    </div>
  );
}

function TrackedSection({ sec, sectionRefs }) {
  const bindRef = (fn) => {
    sectionRefs.current[sec.key] = fn;
  };
  return <PersonSectionBound label={sec.label} forLabel={sec.forLabel} bindRef={bindRef} />;
}

function PersonSectionBound({ label, forLabel, bindRef }) {
  const [step, setStep] = useState(0);
  const [profileInfo, setProfileInfo] = useState(null);
  const [searchTitles, setSearchTitles] = useState(null);
  const [platforms, setPlatforms] = useState({});
  const [results, setResults] = useState([]);
  const [banner, setBanner] = useState(null);

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

  bindRef((evt) => {
    switch (evt.type) {
      case "step":
        setStep(evt.step);
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
          return { ...prev, [evt.platform]: next };
        });
        if (evt.status !== "skipped" && evt.status !== "duplicate") {
          setResults((prev) => [evt, ...prev]);
        }
        break;
      case "error":
        setBanner({ kind: "error", text: "Error: " + evt.message });
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
    />
  );
}
