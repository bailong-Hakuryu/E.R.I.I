# Security Policy

## 支持范围

安全修复优先覆盖最新发布的 `0.x` 版本；实验版本不承诺长期补丁窗口。E.R.I.I. 是
角色连续性与长期记忆内核，不是完整产品身份系统，也不提供商业 SLA。

截至 2026-08-03，最新不可移动发布是 `0.4.0a8`；本文同时记录等待最终验收的
`0.4.0b1` 候选边界。候选实现或文档不能被当作已发布安全承诺。

## 私下报告

不要在公开 Issue 中披露可利用漏洞、真实聊天、私人人设、API Key 或个人信息。优先
使用 GitHub 仓库的 Private vulnerability reporting；若不可用，请先通过维护者的
GitHub 主页请求私下沟通渠道。

报告请包含影响版本、攻击前提、最小复现、可能影响和建议修复，并且只测试你有权处理
的数据与系统。

## 数据与模型边界

- FileStorage、SQLite、MemoryPack 和 Lifecycle Backup 默认明文；宿主负责磁盘加密、
  文件权限、备份权限、密钥管理与用户告知。
- `SecuritySanitizer` 的模式过滤和 PII 掩码是有限纵深防御，不是完整 Prompt 注入、
  数据泄漏或内容安全系统。
- 远程 LLM、Embedding 或向量服务可能接收对话和记忆内容。调用前应取得适当授权，
  记录数据区域/留存政策，并避免发送密钥、隐藏推理和无关私人上下文。
- `Agent × User` 范围是领域完整性边界，不是调用者授权。拥有 Storage 或参考服务 owner
  key 的主体可能访问其中全部关系。

## REST 参考服务

参考服务默认监听 `127.0.0.1`，并默认拒绝业务请求，直到配置
`ERII_API_KEY`。该 key 必须至少包含 **32 个 UTF-8 字节**，通过唯一一个
`X-API-Key` header 发送；缺失、错误或重复 header 都被拒绝。它是单一服务所有者
凭据，不是用户身份、角色权限或租户授权。

只有显式 `--allow-unauthenticated-loopback` 才能在回环地址启用免认证开发模式；它
不能与非回环监听组合，也不应放在会把远端流量伪装成本地地址的反向代理后面。非回环
监听还必须显式传入 `--allow-unsafe-network` 和 owner key。参考服务没有内置 TLS、
用户级授权、速率限制、配额或滥用检测；正式部署必须在可信代理/宿主层终止 TLS 并
实施这些控制。

公开 health 路径是 `/api/v1/health`。OpenAPI/文档和 health 可公开访问；不要把其
可达性理解为业务接口已获授权。

REST 请求体硬上限为 8 MiB。MemoryPack 导入每个受控集合最多 10,000 项，所有受控
集合合计最多 25,000 项。这些是输入边界，不是按用户计费/配额系统，也不能替代代理层
连接数、并发和速率限制。服务错误不会向客户端回显数据库路径、SQL、密钥或内部异常。

## Lifecycle 安全边界

- Lifecycle 操作只应在来源静止、没有 Engine/Storage/worker 活跃写入时运行。
- 来源、备份、staging 和目标父目录必须属于可信宿主。`.erii-lifecycle.lock` 只协调
  遵守协议的进程；它不抵抗拥有同一目录写权限的恶意进程，也不是跨租户锁。
- 链接、reparse point、hard link、非普通文件、遗留 `.tmp`、不稳定 SQLite
  WAL/journal 和变化中的来源失败关闭。发布采用 no-replace 和父目录身份复核，但 b1
  不宣称对抗任意跨进程路径替换。
- File/tree 哈希与复制采用至多 1 MiB 分块。MemoryPack 生命周期输入最多 256 MiB，
  需要物化的 transform 最多 512 MiB，backup manifest 最多 16 MiB。宿主仍应在进入
  内核前限制不可信上传、磁盘用量和并发。
- Lifecycle Backup v1、plan digest、semantic digest 和 MemoryPack commitment 使用
  SHA-256 检测损坏/漂移；它们没有签名或 MAC，不能证明创建者、来源真实性或未被能
  整体重写文件的人篡改。
- 删除是 backup-first：成功报告证明选定 live store 已转换并验证，但预删除 backup
  仍含原数据。外部向量库、已导出 Pack、复制的数据库、日志、云备份、模型提供商与
  其他副本不会自动删除；报告以 `delegated` / `unverified_external` 明示这类责任。
- POSIX 目录 `fsync` 失败会失败关闭。CPython 在 Windows 上没有完全等价的可移植
  目录句柄刷新，因而不承诺相同的掉电持久性；文件刷新和 no-replace 仍然执行。

## 正式产品仍需补齐

生产产品应在内核外实现并验证：每用户身份认证、对象级授权、租户隔离、TLS、加密与
密钥轮换、签名/MAC、速率限制、配额、审计、滥用检测、外部副本删除编排、保留期限与
数据主体请求流程。这些能力在真正交付前不会被 README 或 release notes 宣称为已有。

操作细节见 [`docs/data-lifecycle.md`](docs/data-lifecycle.md)，格式支持见
[`docs/compatibility.md`](docs/compatibility.md)。
