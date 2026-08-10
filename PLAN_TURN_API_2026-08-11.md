# E.R.I.I. 开发计划 - Turn 生命周期 API 完善

**开始日期：** 2026-08-11  
**目标：** 完善 Turn 生命周期 API（v0.5.0a2 路线图 P1 任务）  
**预计工时：** 2-3 天

---

## 项目定位回顾

### E.R.I.I. 的核心使命
> 在长期、独立的 `Agent × User` 关系中，角色能够记得正确的经历、保持自己的底色，
> 并且只因可追溯的真实经历而成长；迁移、删除和模型更换不能伪造或切断这条因果链。

### 目标用户
- AI 伴侣、角色扮演开发者
- 互动叙事、虚拟角色团队
- 重视本地数据、自托管的用户
- 已有 Python 聊天宿主的开发者

### 架构定位
- ✅ 可嵌入的 Python 库
- ✅ 内核演进轨（Kernel Evolution Track）
- ❌ 不是通用 RAG 或 SaaS 平台

---

## 当前状态分析

### Turn 生命周期现有 API

**已实现且稳定（20 个测试全部通过）：**
```python
# 两阶段模式（推荐）
engine.begin_turn(agent_id, user_id, user_message, turn_id=...)
engine.complete_turn(agent_id, user_id, turn_id, agent_message, ...)
engine.abandon_turn(agent_id, user_id, turn_id)

# 一次性模式
engine.record_turn(agent_id, user_id, user_message, agent_message, ...)

# 查询
engine.get_turn(agent_id, user_id, turn_id)
engine.list_turns(agent_id, user_id, status=...)

# 处理
engine.archive_turn(agent_id, user_id, turn_id, ...)
engine.process_relationship_turn(agent_id, user_id, turn_id, ...)
```

**核心特性（已验证）：**
- ✅ Turn Context 快照（一次性获取，不依赖 legacy getters）
- ✅ 并发写入保护（exactly-one-winner）
- ✅ 幂等性（重试安全）
- ✅ MemoryPack 序列化（完整来源转录）
- ✅ 关系隔离（Agent × User 边界）

---

## 需要完善的部分

### 1. 文档完善（优先级最高）

**当前状态：**
- ✅ `docs/host-integration.md` 已有基础文档
- ⚠️ 缺少完整的 API 参考
- ⚠️ 缺少错误处理指南
- ⚠️ 缺少高级用法示例

**任务清单：**
- [ ] 创建 `docs/api/turn-lifecycle.md` - 完整 API 参考
- [ ] 补充错误场景和处理方式
- [ ] 添加完整的代码示例
- [ ] 创建故障排查指南

**预计工时：** 1 天

### 2. 错误处理增强

**当前观察：**
- ✅ 基础错误已定义（TurnConflictError, TurnTerminalConflictError）
- ⚠️ 错误消息可以更详细
- ⚠️ 重试策略需要文档化

**任务清单：**
- [ ] 审计所有 Turn 相关异常
- [ ] 增强错误消息（包含上下文信息）
- [ ] 文档化重试策略
- [ ] 添加错误恢复示例

**预计工时：** 0.5 天

### 3. 测试覆盖增强

**当前状态：**
- ✅ 20 个核心测试通过
- ⚠️ 边缘案例覆盖不足
- ⚠️ 性能测试缺失

**任务清单：**
- [ ] 添加边缘案例测试（网络故障、超时等）
- [ ] 添加性能测试（大量 Turn 场景）
- [ ] 添加并发压力测试
- [ ] 验证 MemoryPack 兼容性

**预计工时：** 1 天

### 4. 辅助功能

**可选增强：**
- [ ] Turn 批量操作（batch_archive, batch_process）
- [ ] Turn 统计查询（count_by_status, get_metrics）
- [ ] Turn 搜索和过滤增强

**预计工时：** 0.5 天（如果需要）

---

## 实施计划

### Day 1（2026-08-11）：文档和 API 参考

**上午：**
1. 创建 `docs/api/turn-lifecycle.md`
2. 完整的 API 参考（所有方法、参数、返回值）
3. 代码示例（基础用法）

**下午：**
1. 错误处理文档
2. 高级用法示例
3. 故障排查指南

**交付物：**
- 完整的 Turn API 文档
- 5+ 个代码示例

### Day 2（2026-08-12）：错误处理和测试

**上午：**
1. 审计和增强错误消息
2. 添加错误恢复示例
3. 文档化重试策略

**下午：**
1. 添加边缘案例测试
2. 添加性能测试
3. 验证所有测试通过

**交付物：**
- 更清晰的错误处理
- 10+ 个新测试用例

### Day 3（2026-08-13）：验证和收尾

**上午：**
1. 运行完整测试套件
2. 性能基准验证
3. 文档审查

**下午：**
1. 创建示例项目
2. 更新 CHANGELOG
3. Git 提交和总结

**交付物：**
- v0.5.0a2 Turn API 完整实现
- 完整文档和测试

---

## 不做的事（明确边界）

根据项目定位，以下内容**不属于**此任务范围：

❌ **不做：**
1. REST API 端点（属于可选 HTTP 包装）
2. GraphQL API（不符合项目定位）
3. WebSocket 实时同步（超出范围）
4. 多租户支持（单用户设计）
5. TypeScript SDK 发布（已暂缓）
6. Go/Rust SDK（延后）
7. Redis 分布式（不符合可嵌入理念）

✅ **专注：**
1. Python 核心 API 稳定性
2. 文档完整性
3. 测试覆盖
4. 错误处理
5. 性能验证

---

## 成功标准

### 功能完整性
- ✅ 所有现有测试通过（20/20）
- ✅ 新增测试通过（目标：+10）
- ✅ 文档完整（API 参考 + 示例 + 故障排查）

### 质量标准
- ✅ 代码覆盖率保持或提升
- ✅ 性能无回归
- ✅ 错误消息清晰可操作

### 文档标准
- ✅ 完整的 API 参考
- ✅ 至少 5 个代码示例
- ✅ 错误处理指南
- ✅ 故障排查文档

---

## 下一步（Day 1 开始）

**明天开始：**

1. **创建 API 文档框架**
   ```bash
   touch docs/api/turn-lifecycle.md
   ```

2. **梳理所有 Turn API**
   - begin_turn()
   - complete_turn()
   - abandon_turn()
   - record_turn()
   - get_turn()
   - list_turns()
   - archive_turn()
   - process_relationship_turn()

3. **为每个 API 编写：**
   - 功能描述
   - 参数说明
   - 返回值
   - 异常
   - 代码示例

---

## 里程碑

**完成后：**
- v0.5.0a2 进度：55% → **70%**
- Turn API 文档：0% → **100%**
- Turn API 测试：20 → **30+**
- 项目健康：8.8/10 → **9.0/10**

---

## 备注

- 专注内核演进轨
- 保持可嵌入设计
- 文档和测试同等重要
- 不追求功能数量，追求质量

---

**计划创建时间：** 2026-08-10 23:00  
**计划执行：** 2026-08-11 开始  
**预计完成：** 2026-08-13  
**状态：** 📋 待执行
