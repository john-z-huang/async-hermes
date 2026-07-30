import { defineConfig } from "vite";

export default defineConfig({
  build: {
    emptyOutDir: true,
    outDir: "dist",
    ssr: "client/src/cli.tsx",
    target: "node22",
  },
  test: { environment: "node" },
});
