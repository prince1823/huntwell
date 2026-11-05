import { Bot, Briefcase, CheckCircle2, LineChart, Settings, UserRound } from "lucide-react";

const TABS = [
  { id: "agent", label: "Agent", icon: Bot },
  { id: "jobs", label: "Review", icon: Briefcase },
  { id: "applied", label: "Applied", icon: CheckCircle2 },
  { id: "tracker", label: "Tracker", icon: LineChart },
  { id: "profile", label: "Profile", icon: UserRound },
  { id: "settings", label: "Settings", icon: Settings },
];

export default function SideNav({ active, onChange }) {
  return (
    <nav className="fixed bottom-0 left-0 top-0 z-30 flex w-[76px] flex-col items-center border-r border-line bg-cream/95 pb-[max(1rem,env(safe-area-inset-bottom))] pt-[max(1.25rem,env(safe-area-inset-top))] backdrop-blur sm:w-20">
      <div className="mb-6 flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-sage text-white">
        <Bot size={20} />
      </div>
      <div className="flex flex-col items-center gap-2">
        {TABS.map(({ id, label, icon: Icon }) => {
          const isActive = id === active;
          return (
            <button
              key={id}
              onClick={() => onChange(id)}
              className={`flex w-16 flex-col items-center gap-1 rounded-2xl px-2 py-2 transition-colors ${
                isActive ? "bg-white text-sage shadow-sm" : "text-ink/40"
              }`}
            >
              <Icon size={20} strokeWidth={isActive ? 2.4 : 1.8} />
              <span className={`text-[10px] ${isActive ? "font-semibold" : "font-medium"}`}>{label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
