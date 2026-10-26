# P0–P4 确定开发基线

> 状态：已实现并通过自动化回归
> 更新日期：2026-10-25

本文记录已经落地的行为，不列候选方案。

## 开发顺序与结果

| 阶段 | 已落地范围 | 验收结果 |
| --- | --- | --- |
| P0 | Policy、不可变 revision、bundle 编译、原子发布、可选可信密钥签名、配置漂移检查 | 发布幂等；文件权限 `0600`；活动策略唯一 |
| P2 | Canonical Action、`record/remind/block`、60 秒一次性 grant、MCP inventory/call、schema/description rug-pull 检查 | 参数、cwd、resource、策略变化、过期、重放和并发重复消费均拒绝 |
| P3A | Claude/Codex Bubblewrap launcher、兼容/严格 profile、启动同边界 canary、能力证据 | fail-closed；工作区外写入和隐藏控制目录访问失败；共享网络明确 `NOT_ENFORCED` |
| P3B | Python/TypeScript SDK；filesystem/process/MCP/network Executor | 经 Executor 的单次结果为 `EXECUTOR_ENFORCED`；session 与绕过路径保持 `OBSERVED` |
| P3C | 数据标签、session taint、MCP/network/model 出口规则、显式 declassification | 敏感读取后未授权出口在上游调用前确定性拒绝；降级有发布规则和证据 |
| P4 | Model/Network Gateway、Credential Broker、OIDC/RBAC、签名灰度计划、响应动作、OTLP/SIEM | 长期密钥不返回 Agent；未知 relay/私网/换域重定向拒绝；响应可逆且留证；导出最小化且幂等 |

## 确定接口

- 策略：`/security/v1alpha1/policies`、revision、publish、rollout、verify。
- 动作：`/security/v1alpha1/actions/evaluate`、`execution-grants/consume`。
- MCP：`/security/v1alpha1/mcp/inventory` 与受控 call。
- Native：session、filesystem、process、MCP、network、model、declassify。
- 响应：session block/unblock、endpoint quarantine/release。
- 管理：role binding、audit export。

## 保证边界

- Native SDK 是调用入口，Executor 才是执行边界。Agent 仍可直接访问宿主能力时，整体 session 不标记 `NATIVE_ENFORCED`。
- 公共 Agent 严格档位中，Codex MCP 可声明 `GATEWAY_ENFORCED`；Claude 因厂商内置 MCP 无法移除而 fail-closed 拒绝严格档位。
- Bubblewrap 第一版共享网络，所以公共 Agent 网络保持 `NOT_ENFORCED`。Native Network Executor 的单次请求可声明 `EXECUTOR_ENFORCED`。
- ACR delivery 未配置时保持 `NOT_CONFIGURED`；灰度 target 为 `pending`，不伪报远端已应用。
- 未配置 `AGENTFACTOR_POLICY_SIGNING_KEY` 的 bundle 不带签名，也不能创建灰度计划。

## 配置项

- `AGENTFACTOR_SECURITY_GATEWAY_CONFIG`：数据面配置路径。
- `AGENTFACTOR_PUBLIC_SANDBOX_PROFILE`：`public-agent-compatibility` 或 `public-agent-strict`。
- `AGENTFACTOR_POLICY_SIGNING_KEY` / `AGENTFACTOR_POLICY_SIGNING_KEY_ID`：bundle 签名。
- `AGENTFACTOR_OIDC_ISSUER` / `AGENTFACTOR_OIDC_AUDIENCE` / `AGENTFACTOR_OIDC_JWKS_URL`：OIDC 校验。
- `AGENTFACTOR_SECURITY_ADMIN_TOKEN`：本机 bootstrap 管理 token。
- `AGENTFACTOR_SIEM_OTLP_ENDPOINT` / `AGENTFACTOR_SIEM_OTLP_TOKEN`：OTLP/HTTP 导出。
- credential policy 的 `source_env`：服务端长期密钥来源；值本身不进入策略或数据库。

## 自动化验收

执行命令：

```bash
PYTHONPATH=src .venv/bin/pytest -q
frontend_fantastic/node_modules/.bin/tsc -p sdk/typescript/tsconfig.json --noEmit
```

本轮最终结果记录在交付说明中。数据库结构由应用 `init_db` 创建；现有环境的迁移命令按项目协作边界仅交付给操作者，不由开发代理执行。
