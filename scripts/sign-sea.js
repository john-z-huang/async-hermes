import { spawnSync } from "node:child_process";
import process from "node:process";

if (process.platform === "darwin") {
  const result = spawnSync("codesign", ["--force", "--sign", "-", "dist/hermes"], {
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
