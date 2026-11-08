import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { createReadStream, statSync } from "node:fs";
import { extname, resolve, sep } from "node:path";

const TYPES: Record<string, string> = {
  ".json": "application/json",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".md": "text/markdown; charset=utf-8",
};

/**
 * Dev-only: serve a PaperCraft project folder read-only at /project/ when
 * PAPERCRAFT_PROJECT is set, so the viewer can open
 * ?scene=project/scene.json straight from the plugin's output.
 * Only files inside that folder with the listed extensions are served.
 */
function projectMount(): Plugin {
  const dir = process.env.PAPERCRAFT_PROJECT;
  return {
    name: "papercraft-project-mount",
    configureServer(server) {
      if (!dir) return;
      const root = resolve(dir);
      server.config.logger.info(`  papercraft: serving ${root} at /project/`);
      server.middlewares.use("/project", (req, res, next) => {
        const rel = decodeURIComponent((req.url ?? "/").split("?")[0]);
        const target = resolve(root, "." + rel);
        const type = TYPES[extname(target).toLowerCase()];
        if (!type || !(target === root || target.startsWith(root + sep))) return next();
        try {
          if (!statSync(target).isFile()) return next();
        } catch {
          return next();
        }
        res.setHeader("Content-Type", type);
        res.setHeader("Cache-Control", "no-store");
        createReadStream(target).pipe(res);
      });
    },
  };
}

// The viewer imports ../schemas/scene.schema.json — the same schema the Python
// plugin validates against — so the dev server must be allowed to read it.
// An exported project gets its own copy of the schema (no paths outside the project).
export default defineConfig({
  base: "./",
  plugins: [react(), projectMount()],
  resolve: {
    alias: {
      "@schemas": fileURLToPath(new URL("../schemas", import.meta.url)),
    },
  },
  server: {
    // localhost only: with PAPERCRAFT_PROJECT the dev server serves your project files.
    // Use `npm run dev -- --host` to test on a phone on your network, knowingly.
    host: "127.0.0.1",
    fs: { allow: [".", "../schemas"] },
  },
  build: {
    chunkSizeWarningLimit: 1500,
  },
});
