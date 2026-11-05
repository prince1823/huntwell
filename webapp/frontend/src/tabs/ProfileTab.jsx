import { useEffect, useMemo, useRef, useState } from "react";
import { FileUp, Sparkles, X } from "lucide-react";
import { addMemoryFact, deleteMemoryFact, fetchMemory, syncMemoryFromResume } from "../api";

const TAG_STYLES = ["bg-sage text-white", "bg-ink text-cream", "border border-line bg-white text-ink"];
const MAX_SHOWN = 10;

// Lays facts out on a circle around the center avatar, like spokes -- even
// angle spacing, with the radius alternating between two rings (odd/even
// index) purely so a dense set of tags doesn't all land on one ring and
// visually merge together.
function layoutPoints(count) {
  return Array.from({ length: count }, (_, i) => {
    const angle = (i / count) * Math.PI * 2 - Math.PI / 2;
    const radius = i % 2 === 0 ? 30 : 41;
    return { x: 50 + radius * Math.cos(angle), y: 50 + radius * Math.sin(angle) * 0.82 };
  });
}

function MemoryFlowgraph({ profile }) {
  const [facts, setFacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [adding, setAdding] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchMemory(profile)
      .then((data) => !cancelled && setFacts(data))
      .catch(() => !cancelled && setFacts([]))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [profile]);

  const shown = facts.slice(0, MAX_SHOWN);
  const extra = facts.length - shown.length;
  const nodeCount = shown.length + (extra > 0 ? 1 : 0);
  const points = useMemo(() => layoutPoints(nodeCount), [nodeCount]);
  const extraPoint = extra > 0 ? points[points.length - 1] : null;

  async function handleAdd(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || adding) return;
    setAdding(true);
    setError("");
    try {
      const fact = await addMemoryFact({ profile, text });
      setFacts((prev) => [...prev, fact]);
      setInput("");
    } catch (err) {
      setError(err.message || "Could not save that.");
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove(id) {
    setFacts((prev) => prev.filter((f) => f.id !== id));
    try {
      await deleteMemoryFact(id, profile);
    } catch {
      // the local list already dropped it -- a stale server copy self-heals on next load
    }
  }

  async function handleResumeFile(e) {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    setSyncing(true);
    setError("");
    try {
      const { facts: updated } = await syncMemoryFromResume({ profile, resumeFile: file });
      setFacts(updated);
    } catch (err) {
      setError(err.message || "Could not read that resume.");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="relative -mx-4 -mt-6 overflow-hidden bg-gradient-to-b from-sage-light/50 via-cream to-cream px-5 py-10 sm:-mx-6 lg:-mx-10 xl:-mx-16">
      <div className="pointer-events-none absolute -left-20 -top-16 h-64 w-64 rounded-full bg-sun/20 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-20 -right-14 h-64 w-64 rounded-full bg-sage/20 blur-3xl" />

      <div className="relative mx-auto flex max-w-2xl flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-ink/40">
            <Sparkles size={12} className="text-sun" /> What your agent knows
          </div>
          <p className="mt-1 max-w-sm text-[11px] text-ink/40">
            Used to score job fit and write tailored answers -- tell it anything it should factor in.
          </p>
        </div>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={syncing}
          className="flex shrink-0 items-center gap-1.5 rounded-full border border-line bg-white/80 px-3 py-1.5 text-xs font-semibold text-ink/60 disabled:opacity-50"
        >
          <FileUp size={13} />
          {syncing ? "Reading resume…" : "Fill from resume"}
        </button>
        <input ref={fileInputRef} type="file" accept=".pdf,.docx,.txt" className="hidden" onChange={handleResumeFile} />
      </div>

      {loading ? (
        <div className="py-10 text-center text-xs text-ink/35">Loading…</div>
      ) : (
        <div className="relative mx-auto aspect-square w-full max-w-[520px]">
          {nodeCount > 0 && (
            <svg viewBox="0 0 100 100" className="absolute inset-0 h-full w-full" preserveAspectRatio="none">
              {points.map((p, i) => (
                <line
                  key={i}
                  x1="50"
                  y1="50"
                  x2={p.x}
                  y2={p.y}
                  stroke="var(--color-line)"
                  strokeWidth="0.6"
                  strokeDasharray="2 2"
                />
              ))}
            </svg>
          )}

          <div className="absolute left-1/2 top-1/2 flex h-16 w-16 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-sage text-xl font-bold text-white shadow-md ring-4 ring-sage-light">
            {(profile || "Y")[0].toUpperCase()}
          </div>

          {shown.map((f, i) => (
            <div
              key={f.id}
              className={`group absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-full px-3 py-1.5 text-xs font-semibold shadow-sm ${TAG_STYLES[i % TAG_STYLES.length]}`}
              style={{ left: `${points[i].x}%`, top: `${points[i].y}%` }}
              title={f.text}
            >
              {f.tag}
              <button
                type="button"
                onClick={() => handleRemove(f.id)}
                className="ml-1 hidden align-middle opacity-70 hover:opacity-100 group-hover:inline"
              >
                <X size={10} />
              </button>
            </div>
          ))}

          {extraPoint && (
            <div
              className="absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap rounded-full border border-line bg-white px-3 py-1.5 text-xs font-semibold text-ink/50"
              style={{ left: `${extraPoint.x}%`, top: `${extraPoint.y}%` }}
            >
              +{extra} more
            </div>
          )}

          {nodeCount === 0 && (
            <div className="absolute inset-x-6 top-[68%] text-center text-xs text-ink/35">
              Nothing yet -- tell it something below, or fill from your resume.
            </div>
          )}
        </div>
      )}

      <form onSubmit={handleAdd} className="relative mx-auto mt-4 flex max-w-2xl items-center gap-2 rounded-full border border-line bg-white px-3.5 py-2 shadow-sm">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="e.g. only FinTech roles, prefer remote, target 25 LPA+"
          className="w-full bg-transparent text-sm outline-none placeholder:text-ink/30"
        />
        <button
          type="submit"
          disabled={!input.trim() || adding}
          className="shrink-0 rounded-full bg-ink px-3.5 py-1.5 text-xs font-semibold text-cream disabled:opacity-40"
        >
          {adding ? "Adding…" : "Add"}
        </button>
      </form>
      {error && <p className="relative mx-auto mt-1.5 max-w-2xl text-[11px] text-red-600">{error}</p>}
    </div>
  );
}

export default function ProfileTab({ profile, onProfileChange }) {
  return (
    <div className="mx-auto max-w-md px-4 pb-10 pt-6 sm:max-w-2xl sm:px-6 lg:max-w-none lg:px-10 xl:px-16">
      <MemoryFlowgraph profile={profile} />
    </div>
  );
}
