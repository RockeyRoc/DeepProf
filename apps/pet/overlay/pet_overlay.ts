import { PetDirector, type PetEvent, type PetState } from "../director";

export interface PetOverlayPort {
  setState(state: PetState): void;
  send(command: { type: "pet.interact" | "voice.transcript"; payload: Record<string, unknown> }): Promise<void>;
}

/** 供 Electron 浮窗和测试使用的无 UI 适配层。 */
export class PetOverlayController {
  readonly director = new PetDirector();

  constructor(private readonly port: PetOverlayPort) {}

  accept(event: PetEvent): void {
    this.port.setState(this.director.accept(event));
  }

  async interact(kind = "open_workspace"): Promise<void> {
    await this.port.send({ type: "pet.interact", payload: { kind } });
  }
}
