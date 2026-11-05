import { useState } from "react";
import BottomNav from "./components/BottomNav";
import AgentTab from "./tabs/AgentTab";
import JobsTab from "./tabs/JobsTab";
import AppliedTab from "./tabs/AppliedTab";
import TrackerTab from "./tabs/TrackerTab";
import ProfileTab from "./tabs/ProfileTab";

export default function App() {
  const [tab, setTab] = useState("agent");
  const [profile, setProfile] = useState("");

  return (
    <div className="min-h-screen bg-cream">
      {tab === "agent" && <AgentTab />}
      {tab === "jobs" && <JobsTab />}
      {tab === "applied" && <AppliedTab profile={profile || undefined} />}
      {tab === "tracker" && <TrackerTab profile={profile || undefined} />}
      {tab === "profile" && <ProfileTab profile={profile} onProfileChange={setProfile} />}
      <BottomNav active={tab} onChange={setTab} />
    </div>
  );
}
