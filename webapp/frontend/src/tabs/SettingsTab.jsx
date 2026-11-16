import { useEffect, useState } from "react";
import { Check, KeyRound, Settings as SettingsIcon, Sliders } from "lucide-react";
import { fetchSettings, saveSettings } from "../api";
import { PLATFORM_IDS, PLATFORM_LABELS } from "../platforms";

const PLATFORM_NOTES = {
  ashby: "guest apply, no login needed",
  workable: "guest apply, no login needed",
  rippling: "guest apply, no login needed",
};

function SectionCard({ icon: Icon, title, subtitle, children }) {
  return (
    <div className="rounded-3xl border border-line bg-white p-5">
      <div className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-ink/40">
        <Icon size={14} className="text-sage" /> {title}
      </div>
      {subtitle && <p className="mb-4 text-[11px] text-ink/40">{subtitle}</p>}
      <div className={subtitle ? "" : "mt-4"}>{children}</div>
    </div>
  );
}

export default function SettingsTab() {
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);

  const [platforms, setPlatforms] = useState({});
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [model, setModel] = useState("");
  const [maxCap, setMaxCap] = useState("");

  const [savingPlatforms, setSavingPlatforms] = useState(false);
  const [savingKey, setSavingKey] = useState(false);
  const [savingModel, setSavingModel] = useState(false);
  const [savingCap, setSavingCap] = useState(false);
  const [savedFlash, setSavedFlash] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetchSettings()
      .then((data) => {
        setSettings(data);
        setPlatforms(data.platforms);
        setModel(data.model);
        setMaxCap(data.max_applications_per_platform_per_day || "");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  function flash(label) {
    setSavedFlash(label);
    setTimeout(() => setSavedFlash(""), 1800);
  }

  async function togglePlatform(id) {
    const next = { ...platforms, [id]: !platforms[id] };
    setPlatforms(next);
    setSavingPlatforms(true);
    setError("");
    try {
      await saveSettings({ platforms: next });
      flash("Platforms saved");
    } catch (e) {
      setError(e.message);
      setPlatforms(platforms);
    } finally {
      setSavingPlatforms(false);
    }
  }

  async function handleSaveKey(e) {
    e.preventDefault();
    setSavingKey(true);
    setError("");
    try {
      const updated = await saveSettings({ apiKey: apiKeyInput });
      setSettings((prev) => ({ ...prev, api_key: updated.api_key }));
      setApiKeyInput("");
      flash(apiKeyInput ? "Key saved" : "Key cleared");
    } catch (e) {
      setError(e.message);
    } finally {
      setSavingKey(false);
    }
  }

  async function handleModelChange(next) {
    setModel(next);
    setSavingModel(true);
    setError("");
    try {
      await saveSettings({ model: next });
      flash("Model saved");
    } catch (e) {
      setError(e.message);
    } finally {
      setSavingModel(false);
    }
  }

  async function handleCapBlur() {
    const n = Number(maxCap);
    if (!n || n <= 0) return;
    setSavingCap(true);
    setError("");
    try {
      await saveSettings({ maxApplicationsPerPlatformPerDay: n });
      flash("Saved");
    } catch (e) {
      setError(e.message);
    } finally {
      setSavingCap(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-md px-4 pb-10 pt-6 sm:max-w-2xl sm:px-6 lg:max-w-none lg:px-10 xl:px-16">
        <div className="py-20 text-center text-sm text-ink/40">Loading…</div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-md px-4 pb-10 pt-6 sm:max-w-2xl sm:px-6 lg:max-w-none lg:px-10 xl:px-16">
      <div className="mb-5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <SettingsIcon size={20} className="text-sage" />
          <h1 className="text-lg font-bold text-ink">Settings</h1>
        </div>
        {savedFlash && (
          <div className="flex items-center gap-1 rounded-full bg-sage-light px-3 py-1 text-xs font-semibold text-sage">
            <Check size={12} /> {savedFlash}
          </div>
        )}
      </div>

      {error && <div className="mb-4 rounded-2xl border border-red-300 bg-red-50 p-3 text-xs text-red-700">{error}</div>}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SectionCard icon={Sliders} title="Platforms to apply on" subtitle="Toggle which job boards a run searches and applies through -- no need to edit config files.">
          <div className="flex flex-col gap-2">
            {PLATFORM_IDS.map((id) => (
              <label
                key={id}
                className="flex cursor-pointer items-center justify-between rounded-2xl border border-line bg-cream px-3.5 py-2.5"
              >
                <div>
                  <div className="text-sm font-medium text-ink">{PLATFORM_LABELS[id]}</div>
                  {PLATFORM_NOTES[id] && <div className="text-[11px] text-ink/40">{PLATFORM_NOTES[id]}</div>}
                </div>
                <input
                  type="checkbox"
                  checked={!!platforms[id]}
                  onChange={() => togglePlatform(id)}
                  disabled={savingPlatforms}
                  className="h-5 w-5 accent-sage"
                />
              </label>
            ))}
          </div>
        </SectionCard>

        <SectionCard icon={KeyRound} title="API key (BYOK)" subtitle="Bring your own OpenAI-compatible key -- overrides the one in .env, used for every parse/score/tailor call.">
          <div className="mb-3 text-xs text-ink/50">
            {settings.api_key.source === "saved" && (
              <>
                Using your saved key <span className="font-mono text-ink/70">{settings.api_key.preview}</span>
              </>
            )}
            {settings.api_key.source === "env" && "Using the key from .env (no key saved here yet)."}
            {settings.api_key.source === "none" && "No key set anywhere -- runs will fail until one is added."}
          </div>
          <form onSubmit={handleSaveKey} className="flex items-center gap-2">
            <input
              type="password"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              placeholder="sk-…"
              className="w-full rounded-2xl border border-line bg-cream px-3.5 py-2.5 text-sm text-ink outline-none placeholder:text-ink/35"
            />
            <button
              type="submit"
              disabled={savingKey}
              className="shrink-0 rounded-full bg-ink px-4 py-2.5 text-xs font-semibold text-cream disabled:opacity-50"
            >
              {savingKey ? "Saving…" : "Save"}
            </button>
          </form>
          {settings.api_key.source === "saved" && (
            <button
              type="button"
              onClick={() => {
                setApiKeyInput("");
                saveSettings({ apiKey: "" }).then((updated) => {
                  setSettings((prev) => ({ ...prev, api_key: updated.api_key }));
                  flash("Key cleared");
                });
              }}
              className="mt-2 text-[11px] font-medium text-ink/40 underline"
            >
              Clear saved key (fall back to .env)
            </button>
          )}
        </SectionCard>

        <SectionCard icon={SettingsIcon} title="Model">
          <select
            value={model}
            onChange={(e) => handleModelChange(e.target.value)}
            disabled={savingModel}
            className="w-full rounded-2xl border border-line bg-cream px-3.5 py-2.5 text-sm text-ink outline-none"
          >
            {settings.available_models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <p className="mt-2 text-[11px] text-ink/40">Used for resume parsing, fit scoring, and every tailored answer.</p>
        </SectionCard>

        <SectionCard icon={Sliders} title="Daily application cap" subtitle="Default max applications per platform per day, across all accounts (per-platform overrides in search_config.yaml still win if set).">
          <input
            type="number"
            min="1"
            value={maxCap}
            onChange={(e) => setMaxCap(e.target.value)}
            onBlur={handleCapBlur}
            placeholder="e.g. 10"
            className="w-32 rounded-2xl border border-line bg-cream px-3.5 py-2.5 text-sm text-ink outline-none placeholder:text-ink/35"
          />
          {savingCap && <span className="ml-2 text-[11px] text-ink/40">Saving…</span>}
        </SectionCard>
      </div>
    </div>
  );
}
