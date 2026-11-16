// The single source of truth for which platforms exist and how to label
// them -- was previously copy-pasted verbatim into five different files.
//
// Ashby/Workable/Rippling stay in this map on purpose -- Applied/Tracker/
// Review still need to label any historical rows from them correctly --
// but they're left out of ACTIVE_PLATFORM_IDS below, which is what the
// Agent tab's run and the Settings tab's toggles actually offer. Their
// guest-apply forms hit CAPTCHAs often enough that they're not reliable
// enough to expose to a casual user yet; the adapters still exist and
// still work from the CLI (--platforms ashby, etc.).
export const PLATFORM_LABELS = {
  linkedin: "LinkedIn",
  wellfound: "Wellfound",
  ashby: "Ashby",
  workable: "Workable",
  rippling: "Rippling",
};

export const PLATFORM_IDS = Object.keys(PLATFORM_LABELS);
export const ACTIVE_PLATFORM_IDS = ["linkedin", "wellfound"];
