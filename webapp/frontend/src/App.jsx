import { useEffect, useState } from "react";
import { KeyRound } from "lucide-react";
import { fetchSettings } from "./api";
import SideNav from "./components/SideNav";
import AgentTab from "./tabs/AgentTab";
import JobsTab from "./tabs/JobsTab";
import AppliedTab from "./tabs/AppliedTab";
import TrackerTab from "./tabs/TrackerTab";
import ProfileTab from "./tabs/ProfileTab";
import SettingsTab from "./tabs/SettingsTab";

export default function App() {
  const [tab, setTab] = useState("agent");
  const [profile, setProfile] = useState("");
  const [needsApiKey, setNeedsApiKey] = useState(false);

  // One-time check on load: a fresh self-hosted deploy has no key in
  // .env and none saved in Settings yet, so every run/scoring/tailoring
  // call would otherwise fail with no visible explanation until someone
  // actually tries to use it. Re-checked whenever you land back on
  // Settings, so the banner clears itself the moment a key is saved.
  useEffect(() => {
    fetchSettings()
      .then((s) => setNeedsApiKey(s.api_key.source === "none"))
      .catch(() => {});
  }, [tab]);

  return (
    <div className="min-h-screen bg-cream pl-[76px] sm:pl-20">
      <SideNav active={tab} onChange={setTab} />
      {needsApiKey && tab !== "settings" && (
        <button
          type="button"
          onClick={() => setTab("settings")}
          className="sticky top-0 z-20 flex w-full items-center justify-center gap-2 bg-ink px-4 py-2 text-xs font-semibold text-cream"
        >
          <KeyRound size={13} />
          No API key set yet -- add one in Settings to get started
        </button>
      )}
      {/* Every tab stays mounted and is just hidden with CSS -- conditionally
          mounting/unmounting used to tear down AgentTab the moment you left
          it, which killed its EventSource and reset all its state (rows,
          sections, run ids), so a run looked like it "stopped" the instant
          you switched tabs even though the backend pipeline kept going
          unseen and un-stoppable from the UI. */}
      <div className={tab === "agent" ? "" : "hidden"}>
        <AgentTab />
      </div>
      <div className={tab === "jobs" ? "" : "hidden"}>
        <JobsTab />
      </div>
      <div className={tab === "applied" ? "" : "hidden"}>
        <AppliedTab profile={profile || undefined} />
      </div>
      <div className={tab === "tracker" ? "" : "hidden"}>
        <TrackerTab profile={profile || undefined} />
      </div>
      <div className={tab === "profile" ? "" : "hidden"}>
        <ProfileTab profile={profile} onProfileChange={setProfile} />
      </div>
      <div className={tab === "settings" ? "" : "hidden"}>
        <SettingsTab />
      </div>
    </div>
  );
}
