import { useEffect, useRef, useState, type CSSProperties } from "react";
import { EventClient } from "../../../../packages/client_sdk/event_client";
import { PetDirector, type PetAction, type PetState } from "../../../pet/director";
import petSheetUrl from "../assets/spritesheet.webp";
import { useLocale } from "./i18n";

const ACTION_ROW: Record<PetAction, number> = {
  idle: 0,
  "running-right": 1,
  "running-left": 2,
  waving: 3,
  jumping: 4,
  failed: 5,
  waiting: 6,
  running: 7,
  review: 8,
};
const ACTIVE_SESSION_KEY = "deepprof.activeSessionId:v1";

function readActiveSession(): string {
  return localStorage.getItem(ACTIVE_SESSION_KEY) || localStorage.getItem("deepprof.activeSessionId") || "";
}

export default function PetApp() {
  const { t } = useLocale();
  const director = useRef(new PetDirector());
  const [state, setState] = useState<PetState>(director.current.state);
  const [runtimeUrl, setRuntimeUrl] = useState("");
  const [sessionId, setSessionId] = useState(readActiveSession);
  const [clickThrough, setClickThrough] = useState(false);
  const sequenceRef = useRef(0);

  useEffect(() => {
    void window.deepprofPet?.bootstrap().then((value) => setRuntimeUrl(value.runtime.baseUrl || ""));
    const update = () => setSessionId(readActiveSession());
    window.addEventListener("storage", update);
    const timer = window.setInterval(update, 1000);
    return () => {
      window.removeEventListener("storage", update);
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!runtimeUrl || !sessionId) return;
    const events = new EventClient({
      baseUrl: runtimeUrl,
      onEvent: (event) => {
        sequenceRef.current = Math.max(sequenceRef.current, event.sequence);
        setState(director.current.accept(event));
      },
    });
    events.connect(sessionId, sequenceRef.current);
    return () => events.close();
  }, [runtimeUrl, sessionId]);

  const spriteStyle = {
    "--sprite-row": ACTION_ROW[state.action],
    "--sprite-col": state.lastSequence % 8,
    backgroundImage: `url(${petSheetUrl})`,
  } as CSSProperties;

  async function toggleClickThrough(): Promise<void> {
    const result = await window.deepprofPet?.setIgnoreMouseEvents(!clickThrough);
    if (result) setClickThrough(result.ignore);
  }

  return (
    <div className={`pet-window action-${state.action}`} title={state.reason}>
      <div className="pet-controls">
        <button type="button" onClick={() => void toggleClickThrough()} aria-pressed={clickThrough}>
          {clickThrough ? "ON" : t("petClickThrough")}
        </button>
      </div>
      <button className="pet-body" onClick={() => void window.deepprofPet?.interact("open_workspace")} aria-label={t("openWorkspace")}>
        <span className="pet-sprite" style={spriteStyle} />
      </button>
      <div className="pet-caption">{t(state.reason)}</div>
    </div>
  );
}
