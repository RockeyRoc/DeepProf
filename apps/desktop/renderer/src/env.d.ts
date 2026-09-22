export {};

declare global {
  interface Window {
    deepprof?: {
      bootstrap: () => Promise<{ runtime: { state: string; baseUrl: string | null }; workbenchUrl: string | null }>;
      runtimeStatus: () => Promise<{ state: string; baseUrl: string | null }>;
      provider: {
        configure: (profile: Record<string, unknown>, secret: string) => Promise<Record<string, unknown>>;
      };
    };
  }
}
