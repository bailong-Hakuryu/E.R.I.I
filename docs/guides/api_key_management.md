# API 密钥管理指南

## 概述

E.R.I.I. v0.5.0a3 使用显式的凭据加载与日志脱敏边界：

- ✅ **仅从环境变量加载密钥**
- ✅ **日志中自动脱敏密钥**
- ✅ **CI/CD 密钥泄露检测**
- ✅ **禁止源码中的字面量密钥**

---

## 快速开始

### 1. 设置环境变量

```bash
# Linux/macOS
export OPENAI_API_KEY="<OPENAI_API_KEY>"
export DEEPSEEK_API_KEY="<DEEPSEEK_API_KEY>"
export GEMINI_API_KEY="<GEMINI_API_KEY>"

# Windows (PowerShell)
$env:OPENAI_API_KEY="<OPENAI_API_KEY>"
$env:DEEPSEEK_API_KEY="<DEEPSEEK_API_KEY>"
$env:GEMINI_API_KEY="<GEMINI_API_KEY>"

# Windows (CMD)
set OPENAI_API_KEY=<OPENAI_API_KEY>
set DEEPSEEK_API_KEY=<DEEPSEEK_API_KEY>
set GEMINI_API_KEY=<GEMINI_API_KEY>
```

### 2. 使用 CredentialManager 加载密钥

```python
from erii.security import CredentialManager

# 自动从 OPENAI_API_KEY 环境变量加载
api_key = CredentialManager.get_api_key("openai")

# 从自定义环境变量加载
custom_key = CredentialManager.get_api_key(
    "my_provider",
    env_var="MY_CUSTOM_API_KEY"
)

# 可选密钥（如果不存在则返回 None）
optional_key = CredentialManager.get_api_key(
    "optional_provider",
    required=False
)
```

### 3. 在 Adapter 中使用

```python
from erii.adapters import OpenAIAdapter

# 推荐方式：自动从环境变量加载
adapter = OpenAIAdapter()  # 从 OPENAI_API_KEY 加载

# 使用自定义环境变量
adapter = OpenAIAdapter(api_key_env="MY_OPENAI_KEY")

# 已弃用：直接传递密钥（会发出警告）
adapter = OpenAIAdapter(api_key="<PROVIDER_API_KEY>")  # 避免硬编码
```

---

## 密钥安全最佳实践

### ✅ 正确做法

1. **使用环境变量**
```python
# ✅ 好：从环境加载
api_key = CredentialManager.get_api_key("openai")
adapter = OpenAIAdapter()
```

2. **使用 .env 文件（开发环境）**
```bash
# .env
OPENAI_API_KEY=<OPENAI_API_KEY>
DEEPSEEK_API_KEY=<DEEPSEEK_API_KEY>
```

```python
# Python 加载 .env
from dotenv import load_dotenv
load_dotenv()

# 现在可以安全使用
from erii.security import CredentialManager
key = CredentialManager.get_api_key("openai")
```

3. **使用密钥管理服务（生产环境）**
```python
# 从 AWS Secrets Manager、Azure Key Vault 等加载
import boto3

client = boto3.client('secretsmanager')
response = client.get_secret_value(SecretId='prod/erii/openai-key')
os.environ['OPENAI_API_KEY'] = response['SecretString']

# 然后正常使用
from erii.security import CredentialManager
key = CredentialManager.get_api_key("openai")
```

### ❌ 错误做法

1. **❌ 在代码中硬编码密钥**
```python
# ❌ 错误！
api_key = "<PROVIDER_API_KEY>"
adapter = OpenAIAdapter(api_key="<PROVIDER_API_KEY>")
```

2. **❌ 在配置文件中存储密钥**
```yaml
# ❌ 错误！config.yaml
openai:
  api_key: <PROVIDER_API_KEY>
```

3. **❌ 在日志中打印密钥**
```python
# ❌ 错误！
logger.info(f"Using API key: {api_key}")
```

---

## 密钥脱敏

### 自动脱敏日志

```python
from erii.security import setup_secure_logging
import logging

logger = logging.getLogger('erii')
setup_secure_logging(logger)

# 现在日志会自动脱敏密钥
logger.info("API key: <PROVIDER_API_KEY>")
# 输出会永久复制凭据，禁止这样做
```

### 手动脱敏

```python
from erii.security import CredentialManager

api_key = CredentialManager.get_api_key("openai")

# 脱敏显示
redacted = CredentialManager.redact_key(api_key)
print(f"Using key: {redacted}")  # 输出只保留短前缀与 ***

# 生成指纹用于调试
fingerprint = CredentialManager.get_key_fingerprint(api_key)
print(f"Key fingerprint: {fingerprint}")  # 输出: Key fingerprint: a3d5e8f1
```

---

## CI/CD 集成

### 密钥泄露检测

项目包含自动密钥泄露检测脚本：

```bash
# 扫描所有源码文件
python scripts/check_key_leakage.py
```

### GitHub Actions 配置示例

```yaml
# .github/workflows/security.yml
name: Security Check

on: [push, pull_request]

jobs:
  key-leakage-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -e .
      
      - name: Check for API key leakage
        run: |
          python scripts/check_key_leakage.py
```

### Git Pre-commit Hook

```bash
# .git/hooks/pre-commit
#!/bin/bash

echo "Checking for API key leakage..."
python scripts/check_key_leakage.py

if [ $? -ne 0 ]; then
    echo "❌ Commit blocked: API key leakage detected!"
    echo "Remove API keys from code and use environment variables."
    exit 1
fi

echo "✅ No API key leakage detected"
```

---

## 多环境配置

### 开发环境

```bash
# .env.development
OPENAI_API_KEY=<LOCAL_TEST_OPENAI_API_KEY>
DEEPSEEK_API_KEY=<LOCAL_TEST_DEEPSEEK_API_KEY>
LOG_LEVEL=DEBUG
```

### 生产环境

```bash
# 生产环境使用密钥管理服务，不使用 .env 文件
# 密钥通过 CI/CD 注入或从 AWS/Azure/GCP Secrets 加载
```

### Docker 配置

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install -e .

# 密钥通过环境变量传入，不写入镜像
# docker run -e OPENAI_API_KEY=<OPENAI_API_KEY> myimage
```

```yaml
# docker-compose.yml
version: '3.8'
services:
  erii:
    build: .
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
    env_file:
      - .env.local  # 本地开发
```

---

## 故障排查

### 问题：找不到 API 密钥

```python
CredentialError: Missing required API key for 'openai'. 
Please set environment variable: OPENAI_API_KEY
```

**解决方案**：
1. 检查环境变量是否设置：
   ```bash
   echo $OPENAI_API_KEY  # Linux/macOS
   echo %OPENAI_API_KEY%  # Windows CMD
   $env:OPENAI_API_KEY    # Windows PowerShell
   ```

2. 在 Python 中验证：
   ```python
   import os
   print(os.environ.get('OPENAI_API_KEY'))
   ```

3. 确保 .env 文件已加载（如果使用）：
   ```python
   from dotenv import load_dotenv
   load_dotenv()
   ```

### 问题：密钥太短

```python
CredentialError: API key for 'openai' is too short (minimum 8 characters).
```

**解决方案**：
- 检查密钥是否完整
- 确认没有额外的空格或换行符
- 最小长度为 8 个字符

### 问题：占位符密钥错误

```python
ValueError: Placeholder API key '<PROVIDER_API_KEY>' is not valid.
```

**解决方案**：
- 不要使用默认的占位符密钥
- 设置真实的 API 密钥到环境变量
- 或使用 `required=False` 使密钥可选

---

## API 参考

### CredentialManager

#### `get_api_key(provider, env_var=None, required=True)`

从环境变量加载 API 密钥。

**参数**：
- `provider` (str): 提供商名称（如 'openai', 'deepseek'）
- `env_var` (str, optional): 自定义环境变量名
- `required` (bool): 是否必需（默认 True）

**返回**：
- API 密钥字符串，或 None（如果 required=False 且未找到）

**异常**：
- `CredentialError`: 如果 required=True 且密钥未找到或无效

#### `redact_key(key, visible_chars=4)`

脱敏 API 密钥用于安全显示。

**参数**：
- `key` (str): 要脱敏的密钥
- `visible_chars` (int): 显示的前缀字符数（默认 4）

**返回**：
- 脱敏后的字符串（如 "prefix***"）

#### `get_key_fingerprint(key)`

生成密钥的稳定指纹用于调试。

**参数**：
- `key` (str): API 密钥

**返回**：
- SHA-256 哈希的前 8 个字符

#### `detect_key_leakage(text)`

检测文本中潜在的密钥泄露。

**参数**：
- `text` (str): 要扫描的文本

**返回**：
- 检测到的潜在密钥列表（已脱敏）

#### `validate_no_literal_keys(code, file_path)`

验证代码中不包含字面量密钥。

**参数**：
- `code` (str): 源代码内容
- `file_path` (str): 文件路径（用于错误报告）

**异常**：
- `CredentialError`: 如果检测到潜在密钥

### RedactingFormatter

日志格式化器，自动脱敏 API 密钥。

```python
from erii.security import RedactingFormatter
import logging

handler = logging.StreamHandler()
handler.setFormatter(RedactingFormatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))
logger = logging.getLogger('myapp')
logger.addHandler(handler)
```

### setup_secure_logging(logger)

为 logger 配置密钥脱敏。

```python
from erii.security import setup_secure_logging
import logging

logger = logging.getLogger('erii')
setup_secure_logging(logger)
```

---

## 迁移指南

### 从旧版本迁移

#### v0.5.0a1 及更早版本

```python
# 旧代码（v0.5.0a1）
adapter = OpenAIAdapter(api_key="<PROVIDER_API_KEY>")

# 新代码（v0.5.0a2+）
# 步骤 1: 设置环境变量
# export OPENAI_API_KEY="<OPENAI_API_KEY>"

# 步骤 2: 移除硬编码密钥
adapter = OpenAIAdapter()
```

#### 自定义 LLM Adapter

```python
# 旧代码
class MyAdapter(BaseLLMAdapter):
    def __init__(self, api_key: str):
        self.api_key = api_key

# 新代码
from erii.security import CredentialManager

class MyAdapter(BaseLLMAdapter):
    def __init__(self, api_key: str = None, api_key_env: str = None):
        if api_key is None:
            self.api_key = CredentialManager.get_api_key(
                provider="my_provider",
                env_var=api_key_env or "MY_PROVIDER_API_KEY"
            )
        else:
            logger.warning("Direct API key is deprecated")
            self.api_key = api_key
```

---

## 相关文档

- [日志和错误处理指南](logging_and_error_handling.md)
- [文档索引](../INDEX.md)

---

*最后更新: 2026-08-08*
