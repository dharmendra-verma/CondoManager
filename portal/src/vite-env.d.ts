// Ambient types for the Vite build-time env vars the portal reads via
// import.meta.env. Declared locally (tsconfig sets "types": [], so vite/client
// is not auto-loaded) to keep `tsc --noEmit` happy without a runtime dep. CM-60.

interface ImportMetaEnv {
  /**
   * Base URL of the agent web-chat API. Empty/undefined = same-origin (local
   * dev, where vite proxies /web -> 127.0.0.1:8000). The prod SWA build sets
   * this to the Container App URL so the browser calls /web/* cross-origin.
   */
  readonly VITE_WEBCHAT_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
