# Public Agent Sandbox

## 目标

公共 Agent 必须进入真实沙箱才能验证 AgentFactor 的安全能力没有破坏 Claude Code、Codex 的登录、Hook、MCP、文件和命令工作流。只对 Native Executor 做隔离测试不能证明 Adapter Mode 可用。

Linux/WSL 第一版使用 Bubblewrap。tmux 仍负责交互和窗口生命周期，但 Claude/Codex 进程由 AgentFactor 的 fail-closed launcher 包装后在 Bubblewrap 内启动。找不到 backend、配置无效或沙箱创建失败时，终端创建失败，不得退回宿主直接执行。

## 第一版边界

- 工作区作为唯一默认可写项目目录；独立 sandbox home、tmp 和 runtime 目录也可写，但不会直接覆盖宿主同名路径。
- 宿主根文件系统只读，并额外隐藏用户 Home 中未声明的区域。
- Agent 使用独立的 sandbox home、tmp、state、cache 和 runtime 目录。
- 只按 Agent 类型暴露运行所需的认证/配置文件；不得挂载整个 `~/.ssh`、云凭据目录或 AgentFactor 数据库目录。
- 使用独立 PID、IPC 和 UTS namespace，限制残留子进程随 launcher 退出。
- 第一版共享网络，以保证公共 Agent 能连接模型服务；网络能力明确标记为 `NOT_ENFORCED`，不能宣传为网络隔离。
- 已安装且显式配置的 ACR Hook 与 AgentFactor MCP 入口继续可见；在完成“无上游直连”的证据校验前，MCP 能力保持 `NOT_ENFORCED`。

Bubblewrap 的宿主只读根和工作区可写挂载提供“项目外写入阻断”，但项目内的直接写入仍可能绕过 Tool Gateway。因此能力必须分开声明：

| 能力 | 第一版保证 |
| --- | --- |
| 工作区外宿主持久文件写入 | `SANDBOX_ENFORCED` |
| 进程/namespace 边界 | `SANDBOX_ENFORCED` |
| 项目内逐文件动作 | `HOOK_ENFORCED` 或 `OBSERVED` |
| MCP | Codex 清空直连配置并只注入 ACR 与 AgentFactor Gateway 时为 `GATEWAY_ENFORCED`；Claude 因厂商内置 Docs 连接仍为 `NOT_ENFORCED` |
| 网络出口 | `NOT_ENFORCED` |
| 宿主凭据 | 只证明未挂载的凭据不可见 |

`record` 和 `remind` 只影响 AgentFactor 策略，不得放宽 Bubblewrap 的硬边界；`block` 在进入沙箱执行前拒绝。

## 生命周期

1. 创建 terminal 并启动 launcher 时生成 `sandbox_id`，记录 backend、profile、workspace 摘要和实际声明能力。
2. tmux 在 workspace 中启动 fail-closed launcher。
3. launcher 验证 workspace、backend 和允许挂载，构造 Bubblewrap argv。
4. Claude/Codex 在沙箱内运行；其子进程继承相同边界。
5. 已配置的 ACR Hook 与 MCP Gateway 继续工作；各自的证明结果独立记录，不由沙箱状态代替。
6. Agent 退出后 Bubblewrap 回收 namespace 和残留进程，AgentFactor 记录结束状态。

启动前选择固定 profile：

- `public-agent-compatibility`：保留第一版兼容边界，各能力按实际证据分别显示。
- `public-agent-strict`：要求 MCP 无不可移除直连。Codex 可在只注入 ACR 与 Security Gateway 时启动；Claude Code 存在无法移除的厂商内置 Docs MCP，因此严格档位直接拒绝启动。

launcher 在启动 Agent 之前、同一个 Bubblewrap 边界内执行 canary：工作区写入必须成功，隐藏的 AgentFactor Home 控制目录和宿主 `/etc` 写入必须失败。状态、时间和最小证据写入 sandbox session。该 canary 不测试网络，所以不会改变网络的 `NOT_ENFORCED` 声明。

## 端到端 canary

离线测试使用假 Agent 验证 argv、挂载和 fail-closed。发布前还必须让本机 Claude Code 与 Codex 各执行一次最小真实任务：

1. 在临时 workspace 创建和修改文件。
2. 运行一个允许的本地命令。
3. 读取 sandbox 未挂载的 Home 隐藏区随机 marker，必须失败。
4. 向 workspace 外写文件，必须失败。
5. 验证 ACR Hook、action fingerprint 和事件链仍有效。
6. 验证 MCP 只暴露 ACR 与 `agentfactor-security-mcp`。
7. 退出后不得残留 Agent 子进程。

真实 canary 会调用模型服务并消耗现有账号额度。测试 workspace 必须是临时目录，不得对真实项目执行破坏性动作。

## 2026-10-23 实现状态

- Claude Code 与 Codex 的 tmux 启动命令已统一经过 fail-closed launcher；缺少 Bubblewrap 或命令无效时不会回退到宿主执行。
- Bubblewrap 使用宿主只读根、独立 Home/tmp/runtime、工作区可写挂载以及 PID/IPC/UTS namespace；Home、AgentFactor 控制目录及其父目录不得作为工作区。
- 认证和必要配置按文件只读挂载；Codex 的 `codex-code-mode-host` 伴随二进制也纳入允许挂载，避免只启动模型但无法执行工具。
- 后端记录 sandbox session，并提供 `/security/v1alpha1/sandboxes` 查询；Web 的 Agent 沙箱页面显示每项实际能力和 `NOT_ENFORCED` 边界。
- 离线 canary 已验证工作区写入成功、未挂载 Home 控制目录不可见、宿主持久目录写入失败。
- 本机 Claude Code 2.1.310 与 Codex 0.148.0 已分别在临时 Bubblewrap 工作区完成真实文件任务。
- 已初始化且安装对应 Hook 的 workspace 自动使用 `acr launch --mode native`，并将会话能力标为 `CONFIGURED`；真实观测证明仍由 ACR capability proof 提供，配置存在本身不会升级成 `HOOK_ENFORCED`。
- Claude/Codex 的原有上游 MCP 配置在启动时被清空或忽略，只注入 ACR directives 和 AgentFactor Security Gateway。Codex 的实际 MCP 清单仅包含这两个 Server，因此显示 `GATEWAY_ENFORCED`；Claude 2.1.310 仍显示厂商内置的 `claude.ai Claude Docs`，因此保持 `NOT_ENFORCED`。
- 临时 ACR workspace 专项 canary 中，Claude 记录 7 个 Hook 事件，Codex 记录 10 个 Hook 事件，事件哈希链均通过。Claude 文件结果仍产生一条“工具输入无法归因”的 coverage gap，所以本轮只证明 Hook 真实运行，不宣称项目内动作已强制闭环。
