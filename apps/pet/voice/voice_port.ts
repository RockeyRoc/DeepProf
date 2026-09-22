export interface VoicePort {
  transcribe(audio: ArrayBuffer): Promise<string>;
}
