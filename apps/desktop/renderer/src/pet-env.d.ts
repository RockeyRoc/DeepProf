export {};

declare global {
  interface Window {
    deepprofPet?: {
      bootstrap: () => Promise<{ runtime: { baseUrl: string | null } }>;
      interact: (kind: string) => Promise<unknown>;
      setIgnoreMouseEvents: (ignore: boolean) => Promise<{ ignore: boolean }>;
    };
  }
}
