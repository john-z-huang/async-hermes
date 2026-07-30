import { ServingStatus, type HealthCheckResponse } from "../generated/v1/agent.js";

export const PROTOCOL_VERSION = "v1";

/** 启动期握手失败：调用方应停止进入可交互状态。 */
export class RpcProtocolError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "RpcProtocolError";
  }
}

/** 确认服务接受请求，且 Node 与 Python 的公开协议版本一致。 */
export function verifyHealthCheck(response: HealthCheckResponse): void {
  if (response.status !== ServingStatus.SERVING_STATUS_SERVING) throw new RpcProtocolError("服务尚未就绪。");
  if (response.protocolVersion !== PROTOCOL_VERSION) {
    throw new RpcProtocolError(`协议版本不兼容：期望 ${PROTOCOL_VERSION}，收到 ${response.protocolVersion}。`);
  }
}
