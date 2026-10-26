# AgentFactor Security Gateway

第三口采用三态执行模型，不让 Agent 的常规高风险工作流等待逐次人工审批：

- `record`：允许并记录最小化决策与执行证据。
- `remind`：允许、记录并产生风险提醒。
- `block`：在执行边界拒绝并产生阻断提醒。

ACR 的 `ASK` 策略结果在宿主边界按 `block` 执行，同时同步为风险提醒。它不会挂起等待人工答复。明确的 ACR `DENY` 仍然不可被 Gateway 放宽。

## Action 与自动授权

`agentfactor.action/v1alpha1` 的 fingerprint 绑定：

- principal、endpoint、Agent 和 integration mode；
- project、session、cwd 和 tool call；
- surface、operation、resource 和完整规范化参数；
- 当前 policy digest。

`record` 和 `remind` 决策自动产生 60 秒一次性 Execution Grant。消费时重新规范化 Action 并计算 fingerprint；参数、cwd、resource 或策略的任何变化都会使 grant 失效。数据库仅保存 token hash，成功消费后不能重放。`block` 不产生 grant。

## 策略发布

默认数据面读取 `~/.conductor/security-gateway.json`，也可以用 `AGENTFACTOR_SECURITY_GATEWAY_CONFIG` 指向另一个文件。控制面通过 `/security/v1alpha1/policies` 创建不可变 revision；publish 编译 bundle，以临时文件、`fsync`、`0600` 权限和原子替换发布数据面配置。`AGENTFACTOR_POLICY_SIGNING_KEY` 存在时 bundle 使用 HMAC-SHA256 签名并记录 `signing_key_id`；未配置密钥时明确返回未签名状态，不能创建灰度计划。

灰度计划对 endpoint ID 和 bundle digest 计算稳定 cohort，只生成 `pending` target，不把 `NOT_CONFIGURED` 的 ACR delivery 宣称为已下发。`GET /security/v1alpha1/policies/{id}/verify` 比对当前数据面配置与已发布 bundle，返回 `healthy` 或 `drifted`。

```json
{
  "policy_version": "2026-10-23.1",
  "default_effect": "block",
  "rules": [
    {
      "id": "developer-workspace-shell",
      "effect": "record",
      "principals": ["terminal-id"],
      "surfaces": ["shell"],
      "operations": ["exec"],
      "resource_prefixes": ["command://scripts/"]
    }
  ],
  "mcp_servers": {
    "internal-tools": {
      "display_name": "Internal tools",
      "command": "/opt/agentfactor/bin/internal-mcp",
      "args": [],
      "tools": {
        "search_docs": {
          "effect": "record"
        },
        "deploy_staging": {
          "effect": "remind",
          "schema_digest": "sha256:replace-with-discovered-digest"
        }
      }
    }
  }
}
```

MCP 配置不接受 `env`，避免把凭据持久化进 AgentFactor。需要凭据的 Server 应从自身的受保护运行环境或后续 Credential Broker 获取。

## MCP Gateway

`agentfactor-security-mcp` 是面向 Agent 的 stdio MCP 入口。它只暴露 inventory 和通用受控调用，实际调用由 AgentFactor API 完成：

1. 连接配置文件中声明的上游 Server。
2. 拉取 `tools/list` 并计算输入 schema digest。
3. 拒绝未列入 allowlist 的 Server/tool。
4. schema 首次出现时固定版本；发生变化后进入 `schema_changed` 并阻断，直到配置显式接受新 digest。
5. 在调用前使用 JSON Schema 校验参数。
6. 自动签发并消费一次性 grant 后才转发 `tools/call`。
7. 中心只保存 schema、参数和结果的 digest，不保存工具参数或返回原文。

Agent 配置必须只指向 `agentfactor-security-mcp`，不能同时保留上游 Server 的直连配置。Gateway、AgentFactor API 或上游不可用时，调用明确失败，不会静默直连。

## Native SDK 与 Executor

Python SDK 位于 `src/agentfactor/sdk`，TypeScript SDK 位于 `sdk/typescript`。两者创建 token-bound session，并把 filesystem、process、MCP、network 和 model 操作送到受控 Executor。session 本身保持 `OBSERVED`；只有某次确实经过 Executor 的返回才标记 `EXECUTOR_ENFORCED`，直接绕过 SDK 的操作不会升级保证。

数据规则为资源分配 `public/internal/source-code/pii/credential/untrusted-content` 标签。读取敏感文件或接收外部 MCP、网络、模型结果会合并到 session taint；MCP、网络和模型出口在调用前校验允许标签。标签只能通过已发布的精确 declassification rule 降级，并保存 purpose digest 与移除标签证据。

Network Executor 只允许已发布 route，逐跳验证协议、域名、端口和全部解析 IP；默认拒绝非公网 IP，使用已验证 IP 建连并保留 TLS SNI，每次重定向重新授权。Agent 提供的 Authorization、Cookie、Host 和代理认证头会被拒绝。

Model Gateway 只允许已发布 provider/model。长期 Provider key 从策略声明的服务端环境变量读取，生成短期内部 credential lease 后注入上游请求；Agent、API 响应和审计库均不返回密钥原文。

## 管理与审计

响应和管理接口接受 OIDC RS256/ES256 bearer token；本机初始化可显式配置 bootstrap token。RBAC 角色为 `viewer`、`policy_author`、`policy_publisher`、`responder` 和 `admin`。session block/unblock、endpoint quarantine/release 都保存操作者、前后状态和原因摘要。

`AGENTFACTOR_SIEM_OTLP_ENDPOINT` 配置后，管理接口可发送最小化 OTLP/HTTP 日志。批次带稳定 idempotency key，本地逐事件记录导出标记；Prompt、文件内容、工具参数、结果原文和认证 token 不进入 payload。
