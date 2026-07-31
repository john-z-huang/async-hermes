import packageJson from "../../package.json";
import { describe, expect, it } from "vitest";

import { releaseManifest } from "./release.js";

describe("发布版本契约", () => {
  it("从共享清单读取 Node、Python 和 Protocol 版本", () => {
    expect(releaseManifest).toMatchObject({
      release_format_version: 1,
      release_version: "0.1.0",
      python_package_version: "0.1.0",
      protocol_version: "v1",
    });
    expect(releaseManifest.node_package_version).toBe(packageJson.version);
  });
});
