export type CliSessionMode = "chat" | "study";
export type CliExperimentGroup = "A" | "B" | "C";

export function newSessionOptions(modeFlag?: string, groupFlag?: string): {
  sessionMode: CliSessionMode;
  group: CliExperimentGroup;
} {
  if (modeFlag && modeFlag !== "chat" && modeFlag !== "study") throw new Error("mode_must_be_chat_or_study");
  if (modeFlag === "chat" && groupFlag) throw new Error("experiment_group_requires_study_mode");
  const sessionMode: CliSessionMode = modeFlag === "study" || Boolean(groupFlag) ? "study" : "chat";
  const rawGroup = (groupFlag || "B").toUpperCase();
  if (sessionMode === "study" && rawGroup !== "A" && rawGroup !== "B" && rawGroup !== "C") {
    throw new Error("group_must_be_A_B_or_C");
  }
  const group: CliExperimentGroup = rawGroup === "A" || rawGroup === "C" ? rawGroup : "B";
  return { sessionMode, group };
}
