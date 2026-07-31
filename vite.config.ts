import type { Plugin } from "vite";
import { defineConfig } from "vite";

/** 修复 Node.js SEA 模式下 ``createRequire(import.meta.url)`` 对内置模块的解析不稳定问题。
 *  在 SEA 中 ``import.meta.url`` 指向虚拟路径，替换为 ``process.execPath``
 *  可保证 ``http2``、``net``、``tls`` 等内置模块始终可解析。 */
function seaRequireFix(): Plugin {
  return {
    name: "sea-require-fix",
    generateBundle(_, bundle) {
      for (const chunk of Object.values(bundle)) {
        if (chunk.type === "chunk") {
          chunk.code = chunk.code.replace(
            /createRequire\(import\.meta\.url\)/g,
            "createRequire(process.execPath)",
          );
        }
      }
    },
  };
}

export default defineConfig({
  plugins: [seaRequireFix()],
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
