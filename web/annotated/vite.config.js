// Builds one self-mounting IIFE into the package's static folder. CSS
// (reactflow, tippy) is emitted beside it and inlined by the template.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const out = fileURLToPath(new URL("../../fairscape_artifacts/static/", import.meta.url));

export default defineConfig({
  plugins: [react()],
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    outDir: out,
    emptyOutDir: false,
    cssCodeSplit: false,
    lib: {
      entry: "src/main.tsx",
      name: "FairscapeAnnotatedGraph",
      formats: ["iife"],
      fileName: () => "annotated_viewer.js",
      cssFileName: "annotated_viewer",
    },
  },
});
