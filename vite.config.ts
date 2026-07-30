import { defineConfig } from "vite";

export default defineConfig({
  ssr: {
    noExternal: true,
  },
  build: {
    emptyOutDir: true,
    outDir: "dist",
    ssr: "client/src/cli.tsx",
    target: "node25",
    rollupOptions: {
      output: {
        format: "es",
        codeSplitting: false,
      },
    },
  },
  test: { environment: "node" },
});
