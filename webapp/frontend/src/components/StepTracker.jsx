const STEPS = ["Extracting resume", "Scoring with AI", "Applying"];

export default function StepTracker({ step }) {
  return (
    <div className="flex gap-2">
      {STEPS.map((label, i) => {
        const n = i + 1;
        const isDone = step > n;
        const isActive = step === n;
        return (
          <div
            key={label}
            className={`flex-1 rounded-2xl border px-3 py-2.5 text-xs transition-colors ${
              isActive
                ? "border-sage bg-sage-light text-ink"
                : isDone
                  ? "border-sage/40 bg-white text-ink"
                  : "border-line bg-white text-ink/40"
            }`}
          >
            <span
              className={`mr-2 inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold ${
                isActive || isDone ? "bg-sage text-white" : "bg-line text-ink/40"
              }`}
            >
              {n}
            </span>
            {label}
          </div>
        );
      })}
    </div>
  );
}
