import { UserRound } from "lucide-react";

export default function ProfileTab({ profile, onProfileChange }) {
  return (
    <div className="mx-auto max-w-md px-4 pb-28 pt-6">
      <div className="mb-5 flex flex-col items-center text-center">
        <div className="flex h-20 w-20 items-center justify-center rounded-full bg-sage-light text-sage">
          <UserRound size={36} />
        </div>
        <div className="mt-3 text-lg font-bold text-ink">{profile || "Your account"}</div>
        <div className="text-xs text-ink/45">{profile ? `Account profile: ${profile}` : "Using your own logged-in browser session"}</div>
      </div>

      <div className="rounded-3xl border border-line bg-white p-4">
        <label className="text-xs font-bold uppercase tracking-wide text-ink/40">Active account</label>
        <input
          value={profile}
          onChange={(e) => onProfileChange(e.target.value)}
          placeholder="blank = your own account"
          className="mt-2 w-full rounded-2xl border border-line bg-cream px-3.5 py-2.5 text-sm text-ink outline-none placeholder:text-ink/35"
        />
        <p className="mt-2 text-[11px] leading-relaxed text-ink/40">
          Leave blank to use your own already-logged-in account. For anyone else, enter the account name set up via{" "}
          <code className="rounded bg-line px-1 py-0.5">--profile &lt;name&gt;</code> on the command line -- that account
          still needs its own one-time login done first.
        </p>
      </div>

      <div className="mt-4 rounded-3xl border border-line bg-white p-4 text-xs leading-relaxed text-ink/55">
        Switching the account here changes which account's stats, applications and tracker you're looking at across the
        Applied and Tracker tabs, and which account a new run on the Agent tab applies as.
      </div>
    </div>
  );
}
