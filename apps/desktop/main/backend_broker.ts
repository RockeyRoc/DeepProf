import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { extname, join, normalize, resolve } from "node:path";

const CONTENT_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".json": "application/json",
};

export class BackendBroker {
  private server: Server | null = null;
  private port: number | null = null;

  constructor(private readonly staticRoot = resolve(__dirname, "../renderer")) {}

  get url(): string | null {
    return this.port ? `http://127.0.0.1:${this.port}` : null;
  }

  async start(): Promise<string> {
    if (this.url) return this.url;
    this.server = createServer((request, response) => {
      const requested = request.url?.split("?")[0] || "/";
      const relative = requested === "/" ? "index.html" : requested.replace(/^\/+/, "");
      const root = resolve(this.staticRoot);
      const file = resolve(normalize(join(root, relative)));
      if (!file.startsWith(`${root}\\`) && !file.startsWith(`${root}/`)) {
        response.writeHead(403).end("forbidden");
        return;
      }
      const target = existsSync(file) && statSync(file).isFile() ? file : join(root, "index.html");
      if (!existsSync(target)) {
        response.writeHead(503).end("workbench_not_built");
        return;
      }
      response.writeHead(200, {
        "content-type": CONTENT_TYPES[extname(target)] || "application/octet-stream",
        "cache-control": "no-store",
      });
      createReadStream(target).pipe(response);
    });
    await new Promise<void>((resolveStart, reject) => {
      this.server?.once("error", reject);
      this.server?.listen(0, "127.0.0.1", () => resolveStart());
    });
    const address = this.server.address();
    this.port = typeof address === "object" && address ? address.port : null;
    if (!this.port) throw new Error("workbench_port_unavailable");
    const url = this.url;
    if (!url) throw new Error("workbench_url_unavailable");
    return url;
  }

  stop(): void {
    this.server?.close();
    this.server = null;
    this.port = null;
  }
}
