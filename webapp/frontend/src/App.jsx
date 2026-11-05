import { useState } from "react";
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

  return (
    <div className="min-h-screen bg-cream pl-[76px] sm:pl-20">
      <SideNav active={tab} onChange={setTab} />
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
