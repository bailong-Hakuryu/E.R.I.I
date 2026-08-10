# E.R.I.I. TypeScript Client

Official TypeScript/JavaScript client library for [E.R.I.I.](https://github.com/bailong-Hakuryu/E.R.I.I) Agent Memory System.

## Features

- 🎯 **Type-safe** - Full TypeScript support with complete type definitions
- 🔒 **Secure** - Built-in API key authentication
- 🚀 **Easy to use** - Simple, intuitive API
- ⚡ **Modern** - Uses async/await, ES modules
- 🛡️ **Error handling** - Structured error responses with retry guidance
- 📦 **Zero dependencies** (except axios)

## Installation

```bash
npm install @erii/client
# or
yarn add @erii/client
# or
pnpm add @erii/client
```

## Quick Start

```typescript
import { ERIIClient } from '@erii/client';

// Initialize client
const client = new ERIIClient({
  apiKey: process.env.ERII_API_KEY!,
  baseURL: 'https://your-erii-server.com'
});

// Remember a conversation
await client.remember({
  user_id: 'user123',
  user_message: 'I love coffee',
  bot_reply: 'Great! I\'ll remember that.'
});

// Recall context
const context = await client.recall({
  user_id: 'user123',
  query: 'what do I like?'
});

console.log(context);
// Output: formatted memory context mentioning coffee
```

## API Reference

### Constructor

```typescript
new ERIIClient(config: ERIIClientConfig)
```

**Config Options:**

```typescript
interface ERIIClientConfig {
  apiKey: string;          // Required: API key for authentication
  baseURL?: string;        // Optional: API server URL (default: http://localhost:8000)
  timeout?: number;        // Optional: Request timeout in ms (default: 30000)
}
```

### Methods

#### `remember(record: TurnRecord): Promise<APIResponse>`

Remember a conversation turn (legacy API).

```typescript
await client.remember({
  agent_id: 'assistant',  // Optional, default: 'default_agent'
  user_id: 'user123',
  user_message: 'Hello!',
  bot_reply: 'Hi there!'
});
```

#### `recall(request: RecallRequest): Promise<string>`

Recall memory context for a query.

```typescript
const context = await client.recall({
  agent_id: 'assistant',  // Optional
  user_id: 'user123',
  query: 'what did we discuss?',
  top_k: 10              // Optional, default based on server
});
```

#### `setCoreMemory(request: CoreMemoryRequest): Promise<APIResponse>`

Set core memory for an agent-user pair.

```typescript
await client.setCoreMemory({
  agent_id: 'assistant',
  user_id: 'user123',
  content: 'User prefers dark mode and coffee'
});
```

#### `getCoreMemory(agentId: string, userId: string): Promise<string>`

Get core memory content.

```typescript
const coreMemory = await client.getCoreMemory('assistant', 'user123');
```

#### `export(agentId: string, userId: string): Promise<any>`

Export memory pack.

```typescript
const memoryPack = await client.export('assistant', 'user123');
// Save to file or transfer
```

#### `import(packData: any, agentId?: string, userId?: string): Promise<APIResponse>`

Import memory pack.

```typescript
await client.import(memoryPack, 'assistant', 'user123');
```

#### `health(): Promise<any>`

Check server health.

```typescript
const status = await client.health();
console.log(status);
// { status: 'healthy', version: '0.5.0a2', timestamp: '...' }
```

## Error Handling

The client throws structured errors with retry guidance:

```typescript
import { ERIIClient, ERIIError } from '@erii/client';

try {
  await client.recall({ user_id: 'user123', query: 'test' });
} catch (error) {
  if (error instanceof ERIIError) {
    console.log('Error code:', error.code);           // 'turn_not_found'
    console.log('Retryable:', error.retryable);       // false
    console.log('Status:', error.statusCode);         // 404
    console.log('Message:', error.message);           // Human-readable
    
    if (error.retryable) {
      // Retry logic
    }
  }
}
```

### Common Error Codes

| Code | HTTP | Meaning | Retryable |
|------|------|---------|-----------|
| `invalid_request` | 400 | Bad request parameters | No |
| `turn_not_found` | 404 | Turn doesn't exist | No |
| `relationship_not_found` | 404 | Relationship not initialized | No |
| `conflict` | 409 | Resource conflict | No |
| `validation_error` | 422 | Data validation failed | No |
| `service_unavailable` | 503 | Service temporarily unavailable | Yes |
| `internal_error` | 500 | Internal server error | Yes |

## Advanced Usage

### Custom Timeout

```typescript
const client = new ERIIClient({
  apiKey: process.env.ERII_API_KEY!,
  baseURL: 'https://api.example.com',
  timeout: 60000  // 60 seconds
});
```

### Multiple Agents

```typescript
// Agent 1
await client.remember({
  agent_id: 'sales_bot',
  user_id: 'user123',
  user_message: 'I need a CRM',
  bot_reply: 'Let me help you with that'
});

// Agent 2
await client.remember({
  agent_id: 'support_bot',
  user_id: 'user123',
  user_message: 'My login is broken',
  bot_reply: 'I can help you reset it'
});

// Each agent maintains separate memory
```

### TypeScript Types

```typescript
import type {
  ERIIClientConfig,
  TurnRecord,
  RecallRequest,
  CoreMemoryRequest,
  ERIIErrorDetail,
  APIResponse
} from '@erii/client';
```

## Examples

### Node.js Backend

```typescript
import { ERIIClient } from '@erii/client';
import express from 'express';

const app = express();
const erii = new ERIIClient({
  apiKey: process.env.ERII_API_KEY!,
  baseURL: process.env.ERII_URL!
});

app.post('/chat', async (req, res) => {
  const { userId, message } = req.body;
  
  // Get context
  const context = await erii.recall({
    user_id: userId,
    query: message
  });
  
  // Generate response with LLM (not shown)
  const reply = await generateReply(context, message);
  
  // Remember the turn
  await erii.remember({
    user_id: userId,
    user_message: message,
    bot_reply: reply
  });
  
  res.json({ reply });
});
```

### React Frontend

```typescript
import { ERIIClient } from '@erii/client';
import { useState } from 'react';

const erii = new ERIIClient({
  apiKey: import.meta.env.VITE_ERII_API_KEY,
  baseURL: 'https://api.example.com'
});

function ChatComponent() {
  const [messages, setMessages] = useState([]);
  
  const sendMessage = async (text: string) => {
    // Recall context
    const context = await erii.recall({
      user_id: 'current_user',
      query: text
    });
    
    // Use context for better responses
    // ...
  };
  
  return <div>{/* UI */}</div>;
}
```

## Requirements

- Node.js 16+ or modern browser with ES2020 support
- TypeScript 5.0+ (for TypeScript projects)

## License

Apache-2.0

## Links

- [E.R.I.I. Main Repository](https://github.com/bailong-Hakuryu/E.R.I.I)
- [Documentation](https://github.com/bailong-Hakuryu/E.R.I.I/tree/main/docs)
- [API Reference](https://github.com/bailong-Hakuryu/E.R.I.I/blob/main/docs/USAGE.md)
- [Report Issues](https://github.com/bailong-Hakuryu/E.R.I.I/issues)

## Contributing

Contributions are welcome! Please see the main repository for contribution guidelines.
