import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// The viewer imports ../schemas/scene.schema.json — the same schema the Python
// plugin validates against — so the dev server must be allowed to read it.
// An exported project gets its own copy of the schema (no paths outside the project).
export default defineConfig({
  base: "./",
  plugins: [react()],
  resolve: {
    alias: {
      "@schemas": fileURLToPath(new URL("../schemas", import.meta.url)),
    },
  },
  server: {
    host: true,
    fs: { allow: [".", "../schemas"] },
  },
  build: {
    chunkSizeWarningLimit: 1500,
  },
});
