/**
 * E.R.I.I. TypeScript Client
 *
 * Official TypeScript/JavaScript client for E.R.I.I. Agent Memory System.
 */

import axios, { AxiosInstance, AxiosError } from 'axios';

/**
 * Error response from E.R.I.I. API
 */
export interface ERIIErrorDetail {
  code: string;
  retryable: boolean;
  safe_summary: string;
}

/**
 * E.R.I.I. API Error
 */
export class ERIIError extends Error {
  public readonly code: string;
  public readonly retryable: boolean;
  public readonly statusCode: number;

  constructor(detail: ERIIErrorDetail, statusCode: number) {
    super(detail.safe_summary);
    this.name = 'ERIIError';
    this.code = detail.code;
    this.retryable = detail.retryable;
    this.statusCode = statusCode;
  }
}

/**
 * Configuration options for E.R.I.I. client
 */
export interface ERIIClientConfig {
  /**
   * API key for authentication
   */
  apiKey: string;

  /**
   * Base URL of E.R.I.I. API server
   * @default "http://localhost:8000"
   */
  baseURL?: string;

  /**
   * Request timeout in milliseconds
   * @default 30000
   */
  timeout?: number;
}

/**
 * Turn record for archival
 */
export interface TurnRecord {
  agent_id?: string;
  user_id: string;
  user_message: string;
  bot_reply: string;
}

/**
 * Recall request parameters
 */
export interface RecallRequest {
  agent_id?: string;
  user_id: string;
  query: string;
  top_k?: number;
}

/**
 * Core memory request
 */
export interface CoreMemoryRequest {
  agent_id?: string;
  user_id: string;
  content: string;
}

/**
 * API response wrapper
 */
export interface APIResponse<T = any> {
  status: string;
  [key: string]: any;
}

/**
 * E.R.I.I. TypeScript Client
 *
 * @example
 * ```typescript
 * import { ERIIClient } from '@erii/client';
 *
 * const client = new ERIIClient({
 *   apiKey: process.env.ERII_API_KEY!,
 *   baseURL: 'https://your-server.com'
 * });
 *
 * // Remember a conversation
 * await client.remember({
 *   user_id: 'user123',
 *   user_message: 'Hello!',
 *   bot_reply: 'Hi there!'
 * });
 *
 * // Recall context
 * const context = await client.recall({
 *   user_id: 'user123',
 *   query: 'what did we talk about?'
 * });
 * ```
 */
export class ERIIClient {
  private readonly client: AxiosInstance;

  constructor(config: ERIIClientConfig) {
    if (!config.apiKey) {
      throw new Error('API key is required');
    }

    this.client = axios.create({
      baseURL: config.baseURL || 'http://localhost:8000',
      timeout: config.timeout || 30000,
      headers: {
        'Authorization': `Bearer ${config.apiKey}`,
        'Content-Type': 'application/json',
      },
    });

    // Add response interceptor for error handling
    this.client.interceptors.response.use(
      (response) => response,
      (error: AxiosError) => {
        if (error.response?.data && typeof error.response.data === 'object') {
          const data = error.response.data as any;
          if (data.detail && typeof data.detail === 'object' && 'code' in data.detail) {
            throw new ERIIError(data.detail, error.response.status);
          }
        }
        throw error;
      }
    );
  }

  /**
   * Remember a conversation turn (legacy API)
   *
   * @param record - Turn record to remember
   * @returns Success response
   *
   * @deprecated Use `recordTurn()` or turn lifecycle APIs instead
   */
  async remember(record: TurnRecord): Promise<APIResponse> {
    const response = await this.client.post('/api/v1/remember', {
      agent_id: record.agent_id || 'default_agent',
      ...record,
    });
    return response.data;
  }

  /**
   * Recall memory context for a query
   *
   * @param request - Recall parameters
   * @returns Formatted context string
   */
  async recall(request: RecallRequest): Promise<string> {
    const response = await this.client.post('/api/v1/recall', {
      agent_id: request.agent_id || 'default_agent',
      ...request,
    });
    return response.data.context;
  }

  /**
   * Set core memory for an agent-user pair
   *
   * @param request - Core memory content
   * @returns Success response
   */
  async setCoreMemory(request: CoreMemoryRequest): Promise<APIResponse> {
    const response = await this.client.post('/api/v1/core_memory', {
      agent_id: request.agent_id || 'default_agent',
      ...request,
    });
    return response.data;
  }

  /**
   * Get core memory for an agent-user pair
   *
   * @param agentId - Agent ID
   * @param userId - User ID
   * @returns Core memory content
   */
  async getCoreMemory(agentId: string, userId: string): Promise<string> {
    const response = await this.client.get('/api/v1/core_memory', {
      params: {
        agent_id: agentId || 'default_agent',
        user_id: userId,
      },
    });
    return response.data.content;
  }

  /**
   * Export memory pack for an agent-user pair
   *
   * @param agentId - Agent ID
   * @param userId - User ID
   * @returns Memory pack data
   */
  async export(agentId: string, userId: string): Promise<any> {
    const response = await this.client.post('/api/v1/export', {
      agent_id: agentId || 'default_agent',
      user_id: userId,
    });
    return response.data.pack_data;
  }

  /**
   * Import memory pack
   *
   * @param packData - Memory pack data
   * @param agentId - Optional agent ID override
   * @param userId - Optional user ID override
   * @returns Success response
   */
  async import(packData: any, agentId?: string, userId?: string): Promise<APIResponse> {
    const response = await this.client.post('/api/v1/import', {
      pack_data: packData,
      agent_id: agentId,
      user_id: userId,
    });
    return response.data;
  }

  /**
   * Health check
   *
   * @returns Health status
   */
  async health(): Promise<any> {
    const response = await this.client.get('/health');
    return response.data;
  }
}

// Re-export types
export type {
  ERIIClientConfig,
  TurnRecord,
  RecallRequest,
  CoreMemoryRequest,
  ERIIErrorDetail,
  APIResponse,
};
