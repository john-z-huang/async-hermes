import rawManifest from "../../hermes/release_manifest.json";

export interface ReleaseManifest {
  release_format_version: number;
  release_version: string;
  node_package_version: string;
  python_package_version: string;
  protocol_version: string;
}

function releaseManifestFrom(value: unknown): ReleaseManifest {
  if (!value || typeof value !== "object") throw new Error("发布清单格式不受支持。");
  const manifest = value as Partial<ReleaseManifest>;
  if (
    manifest.release_format_version !== 1 ||
    !manifest.release_version ||
    !manifest.node_package_version ||
    !manifest.python_package_version ||
    !manifest.protocol_version
  ) {
    throw new Error("发布清单缺少必填版本字段。");
  }
  return manifest as ReleaseManifest;
}

/** Node bundle 内嵌的发布契约；构建期与 Python 包使用同一 JSON 源。 */
export const releaseManifest = releaseManifestFrom(rawManifest);
