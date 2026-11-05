import { Bot, Briefcase, CheckCircle2, LineChart, UserRound } from "lucide-react";

const TABS = [
  { id: "agent", label: "Agent", icon: Bot },
  { id: "jobs", label: "Jobs", icon: Briefcase },
  { id: "applied", label: "Applied", icon: CheckCircle2 },
  { id: "tracker", label: "Tracker", icon: LineChart },
  { id: "profile", label: "Profile", icon: UserRound },
];

export default function BottomNav({ active, onChange }) {
  return (
    <nav className="fixed bottom-0 left-0 right-0 z-30 border-t border-line bg-cream/95 backdrop-blur">
      <div className="mx-auto flex max-w-md items-stretch justify-between px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-1.5">
        {TABS.map(({ id, label, icon: Icon }) => {
          const isActive = id === active;
          return (
            <button
              key={id}
              onClick={() => onChange(id)}
              className={`flex flex-1 flex-col items-center gap-1 rounded-2xl px-2 py-1.5 transition-colors ${
                isActive ? "text-sage" : "text-ink/40"
              }`}
            >
              <Icon size={22} strokeWidth={isActive ? 2.4 : 1.8} />
              <span className={`text-[11px] ${isActive ? "font-semibold" : "font-medium"}`}>{label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
