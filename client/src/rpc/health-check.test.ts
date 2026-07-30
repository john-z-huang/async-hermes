import { describe, expect, it } from "vitest";

import { ServingStatus, type HealthCheckResponse } from "../generated/v1/agent.js";
import { RpcProtocolError, verifyHealthCheck } from "./health-check.js";

const serving: HealthCheckResponse = {
  status: ServingStatus.SERVING_STATUS_SERVING,
  protocolVersion: "v1",
};

describe("verifyHealthCheck", () => {
  it("接受就绪且协议版本兼容的服务", () => {
    expect(() => verifyHealthCheck(serving)).not.toThrow();
  });

  it("以稳定错误区分未就绪和协议不兼容", () => {
    expect(() => verifyHealthCheck({ ...serving, status: ServingStatus.SERVING_STATUS_NOT_SERVING })).toThrow(
      "服务尚未就绪。",
    );
    expect(() => verifyHealthCheck({ ...serving, protocolVersion: "v2" })).toThrow(RpcProtocolError);
    expect(() => verifyHealthCheck({ ...serving, protocolVersion: "v2" })).toThrow(
      "协议版本不兼容：期望 v1，收到 v2。",
    );
  });
});
