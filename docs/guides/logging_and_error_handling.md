# 日志和错误处理指南

## 概述

E.R.I.I. v0.5.0a2+ 提供了增强的日志和错误处理系统，包括：

- ✅ **结构化日志** - 支持文本和 JSON 格式
- ✅ **自动密钥脱敏** - 防止敏感信息泄露
- ✅ **审计日志** - 记录关键操作
- ✅ **性能监控** - 追踪操作耗时
- ✅ **丰富的错误上下文** - 错误码、严重性、恢复建议
- ✅ **错误链追踪** - 保留原始异常信息

---

## 日志系统

### 基础日志

```python
from erii.core.logging import StructuredLogger, LogLevel

# 获取logger
logger = StructuredLogger.get_logger("my_module", level=LogLevel.INFO)

# 记录不同级别的日志
logger.debug("调试信息")
logger.info("一般信息")
logger.warning("警告信息")
logger.error("错误信息")
logger.critical("严重错误")
```

### JSON 格式日志

适用于日志聚合系统（如 ELK、Splunk）：

```python
from erii.core.logging import StructuredLogger, LogFormat

logger = StructuredLogger.get_logger(
    "my_module",
    format_type=LogFormat.JSON
)

logger.info("用户操作", extra={
    'user_id': 'alice',
    'action': 'recall',
    'duration_ms': 45.67
})
```

输出：
```json
{
  "timestamp": "2026-08-08T10:20:28.506702+00:00",
  "level": "INFO",
  "logger": "my_module",
  "message": "用户操作",
  "user_id": "alice",
  "action": "recall",
  "duration_ms": 45.67
}
```

### 配置日志系统

```python
from erii.core.logging import StructuredLogger

config = {
    'level': 'INFO',           # 日志级别
    'format': 'json',          # 输出格式 (text/json)
    'file': '/var/log/erii.log',  # 日志文件路径
    'max_bytes': 10485760,     # 文件大小限制 (10MB)
    'backup_count': 5          # 保留备份数
}

StructuredLogger.configure_from_dict(config)
```

### 审计日志

记录安全相关和数据修改操作：

```python
from erii.core.logging import AuditLogger

audit = AuditLogger()

# 记录关系初始化
audit.log_relationship_init(
    relationship_id="rel-123",
    agent="my-agent",
    user="alice",
    status="success"
)

# 记录人格决策
audit.log_persona_decision(
    relationship_id="rel-123",
    proposal_id="prop-456",
    decision="approve"
)

# 记录数据导入
audit.log_data_import(
    source_type="memorypack",
    record_count=100
)

# 记录数据删除
audit.log_data_deletion(
    deletion_type="relationship",
    scope="rel-123",
    record_count=50
)
```

### 性能监控

```python
from erii.core.logging import PerformanceLogger

perf = PerformanceLogger()

# 手动记录耗时
perf.log_timing("recall_operation", 123.45, relationship_id="rel-123")

# 使用上下文管理器自动计时
with perf.timer("recall", relationship_id="rel-123"):
    # 执行操作
    result = engine.recall(...)
```

### 全局日志实例

```python
from erii.core.logging import (
    get_logger,
    get_audit_logger,
    get_performance_logger
)

# 默认logger
logger = get_logger()
logger.info("操作完成")

# 审计logger
audit = get_audit_logger()
audit.log_operation("custom_op", status="success")

# 性能logger
perf = get_performance_logger()
with perf.timer("my_operation"):
    # 代码
    pass
```

---

## 错误处理系统

### 基础错误处理

```python
from erii.errors import (
    StorageError,
    ErrorCode,
    ErrorSeverity
)

# 抛出错误
raise StorageError(
    "写入数据库失败",
    code=ErrorCode.STORAGE_WRITE_FAILED,
    severity=ErrorSeverity.HIGH,
    context={
        'file_path': '/tmp/data.db',
        'operation': 'write',
        'bytes': 1024
    },
    recovery_hint="检查磁盘空间和文件权限"
)
```

### 错误码

所有错误都有标准错误码，便于程序化处理：

| 错误码范围 | 类别 | 示例 |
|----------|------|------|
| E1xxx | 存储错误 | E1001 (数据损坏), E1002 (写入失败) |
| E2xxx | 格式错误 | E2000 (不支持的格式), E2001 (需要迁移) |
| E3xxx | 生命周期错误 | E3003 (冲突), E3004 (验证失败) |
| E4xxx | 凭据错误 | E4000 (缺少密钥), E4001 (无效密钥) |
| E5xxx | API 错误 | E5000 (连接失败), E5002 (限流) |
| E6xxx | 验证错误 | E6000 (验证失败), E6001 (约束违反) |
| E7xxx | 关系错误 | E7000 (未找到), E7001 (冲突) |
| E9xxx | 内部错误 | E9000 (内部错误), E9002 (配置错误) |

### 常用错误类型

#### 存储错误

```python
from erii.errors import StorageIntegrityError, StorageWriteError

# 数据完整性错误
raise StorageIntegrityError("数据库已损坏")
# 自动包含: severity=CRITICAL, recovery_hint="从备份恢复"

# 写入失败
raise StorageWriteError("无法写入文件")
# 自动包含: severity=HIGH, recovery_hint="检查权限和磁盘空间"
```

#### API 错误

```python
from erii.errors import (
    APIConnectionError,
    APITimeoutError,
    APIRateLimitError,
    APIAuthenticationError
)

# 连接失败
raise APIConnectionError("无法连接到 API")

# 超时
raise APITimeoutError("请求超时")

# 限流
raise APIRateLimitError("超过速率限制")

# 认证失败
raise APIAuthenticationError("API 密钥无效")
```

#### 关系错误

```python
from erii.errors import (
    RelationshipNotFoundError,
    RelationshipUninitializedError
)

# 关系未找到
raise RelationshipNotFoundError(
    f"关系 {relationship_id} 不存在",
    context={'relationship_id': relationship_id}
)

# 关系未初始化
raise RelationshipUninitializedError(
    "请先初始化关系",
    recovery_hint="调用 initialize_relationship()"
)
```

### 包装异常

```python
from erii.errors import StorageError

try:
    # 可能失败的操作
    with open('/tmp/data.json') as f:
        data = json.load(f)
except (IOError, json.JSONDecodeError) as e:
    raise StorageError(
        "无法读取数据文件",
        code=ErrorCode.STORAGE_READ_FAILED,
        context={'file_path': '/tmp/data.json'},
        cause=e  # 保留原始异常
    )
```

### 错误处理模式

#### 1. 捕获并记录

```python
from erii.core.logging import get_logger
from erii.errors import StorageError

logger = get_logger()

try:
    # 操作
    storage.write(data)
except StorageError as e:
    logger.error(
        f"存储操作失败: {e}",
        extra={
            'error_code': e.code,
            'severity': e.severity,
            'context': e.context
        }
    )
    # 重新抛出或返回错误
    raise
```

#### 2. 优雅降级

```python
from erii.errors import APIError, ErrorSeverity

try:
    result = call_external_api()
except APIError as e:
    if e.severity == ErrorSeverity.MEDIUM:
        # 可恢复的错误，使用缓存
        logger.warning(f"API 调用失败，使用缓存: {e}")
        result = get_cached_result()
    else:
        # 严重错误，必须失败
        raise
```

#### 3. 重试逻辑

```python
from erii.errors import APITimeoutError, APIRateLimitError
import time

max_retries = 3
for attempt in range(max_retries):
    try:
        result = api_call()
        break
    except (APITimeoutError, APIRateLimitError) as e:
        if attempt < max_retries - 1:
            wait_time = 2 ** attempt  # 指数退避
            logger.warning(f"重试 {attempt + 1}/{max_retries}: {e}")
            time.sleep(wait_time)
        else:
            logger.error(f"达到最大重试次数: {e}")
            raise
```

#### 4. 错误转换为 HTTP 响应

```python
from erii.errors import ERIIError, ErrorCode, ErrorSeverity
from fastapi import HTTPException

try:
    result = engine.operation()
except ERIIError as e:
    # 根据错误严重性确定 HTTP 状态码
    status_code_map = {
        ErrorSeverity.LOW: 200,
        ErrorSeverity.MEDIUM: 400,
        ErrorSeverity.HIGH: 500,
        ErrorSeverity.CRITICAL: 500,
    }
    
    status_code = status_code_map.get(e.severity, 500)
    
    raise HTTPException(
        status_code=status_code,
        detail=e.to_dict()
    )
```

### 自定义错误

```python
from erii.errors import ERIIError, ErrorCode, ErrorSeverity

class MyCustomError(ERIIError):
    """自定义错误类型。"""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message,
            code=ErrorCode.INTERNAL_ERROR,  # 或自定义错误码
            severity=ErrorSeverity.HIGH,
            **kwargs
        )
```

---

## 最佳实践

### 日志最佳实践

1. **使用适当的日志级别**
   - DEBUG: 详细的诊断信息
   - INFO: 一般信息性消息
   - WARNING: 警告但不影响功能
   - ERROR: 错误但应用继续运行
   - CRITICAL: 严重错误，应用可能停止

2. **提供上下文**
   ```python
   logger.info(
       "召回完成",
       extra={
           'relationship_id': rel_id,
           'user': user,
           'duration_ms': duration,
           'result_count': len(results)
       }
   )
   ```

3. **避免敏感信息**
   - API 密钥会自动脱敏
   - 密码、令牌等敏感字段会被过滤

4. **记录关键操作**
   - 关系初始化
   - 人格批准/拒绝
   - 数据导入/导出/删除
   - API 调用失败

### 错误处理最佳实践

1. **提供丰富的上下文**
   ```python
   raise StorageError(
       "写入失败",
       context={
           'file_path': path,
           'operation': 'write',
           'bytes': len(data),
           'available_space': get_free_space()
       }
   )
   ```

2. **包含恢复建议**
   ```python
   raise ConfigurationError(
       "缺少必需配置",
       recovery_hint="设置 OPENAI_API_KEY 环境变量"
   )
   ```

3. **保留异常链**
   ```python
   try:
       operation()
   except Exception as e:
       raise MyError("操作失败", cause=e)
   ```

4. **使用适当的错误类型**
   - 不要用通用 Exception
   - 选择最具体的错误类型
   - 必要时创建自定义错误

5. **错误不应包含敏感信息**
   - 密钥会自动过滤
   - 避免在错误消息中包含密码、令牌等

---

## 集成示例

### 在 Engine 中集成

```python
from erii.core.logging import get_logger, get_audit_logger, get_performance_logger
from erii.errors import StorageError, RelationshipNotFoundError

class ERIIEngine:
    def __init__(self):
        self.logger = get_logger("erii.engine")
        self.audit = get_audit_logger()
        self.perf = get_performance_logger()
    
    def initialize_relationship(self, agent, user, blueprint):
        """初始化关系并记录审计日志。"""
        with self.perf.timer("initialize_relationship"):
            try:
                self.logger.info(
                    "初始化关系",
                    extra={'agent': agent, 'user': user}
                )
                
                # 执行操作
                relationship_id = self._do_initialize(agent, user, blueprint)
                
                # 审计日志
                self.audit.log_relationship_init(
                    relationship_id=relationship_id,
                    agent=agent,
                    user=user
                )
                
                return relationship_id
                
            except StorageError as e:
                self.logger.error(f"关系初始化失败: {e}")
                raise
```

### 在 REST API 中集成

```python
from fastapi import FastAPI, HTTPException
from erii.core.logging import get_logger
from erii.errors import ERIIError, RelationshipNotFoundError

app = FastAPI()
logger = get_logger("erii.api")

@app.get("/api/v1/relationships/{relationship_id}")
async def get_relationship(relationship_id: str):
    """获取关系快照。"""
    try:
        logger.info(f"获取关系: {relationship_id}")
        
        snapshot = engine.get_relationship_snapshot(relationship_id)
        
        return snapshot
        
    except RelationshipNotFoundError as e:
        logger.warning(f"关系未找到: {relationship_id}")
        raise HTTPException(status_code=404, detail=e.to_dict())
        
    except ERIIError as e:
        logger.error(f"获取关系失败: {e}")
        raise HTTPException(status_code=500, detail=e.to_dict())
```

---

## 配置示例

### 开发环境

```python
# config/development.py
LOG_CONFIG = {
    'level': 'DEBUG',
    'format': 'text',
    'file': None  # 仅控制台输出
}
```

### 生产环境

```python
# config/production.py
LOG_CONFIG = {
    'level': 'INFO',
    'format': 'json',
    'file': '/var/log/erii/app.log',
    'max_bytes': 100 * 1024 * 1024,  # 100MB
    'backup_count': 10
}
```

---

## 故障排查

### 日志未输出

**问题**: 日志消息没有显示

**解决方案**:
1. 检查日志级别设置
2. 确认 logger 已正确配置
3. 检查处理器是否添加

```python
import logging
logger = logging.getLogger('erii')
print(f"Logger level: {logger.level}")
print(f"Handlers: {logger.handlers}")
```

### 日志文件无法写入

**问题**: PermissionError 或 IOError

**解决方案**:
1. 检查文件路径权限
2. 确保目录存在
3. 检查磁盘空间

### 错误信息不完整

**问题**: 错误缺少上下文

**解决方案**:
确保传递 context 参数：

```python
raise StorageError(
    "操作失败",
    context={'key': 'value'}  # 添加上下文
)
```

---

## API 参考

完整 API 文档请参阅：
- [日志 API](../api/logging.md)
- [错误 API](../api/errors.md)

---

*最后更新: 2026-08-08*
