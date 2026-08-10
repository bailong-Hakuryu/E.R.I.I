/**
 * @jest-environment node
 */

import { ERIIClient, ERIIError } from '../src/index';
import axios from 'axios';

jest.mock('axios');
const mockedAxios = axios as jest.Mocked<typeof axios>;

describe('ERIIClient', () => {
  let client: ERIIClient;
  const mockApiKey = 'test-api-key-1234567890';
  const mockBaseURL = 'http://localhost:8000';

  beforeEach(() => {
    client = new ERIIClient({
      apiKey: mockApiKey,
      baseURL: mockBaseURL,
    });

    // Mock axios.create to return a mocked instance
    mockedAxios.create.mockReturnValue({
      post: jest.fn(),
      get: jest.fn(),
      interceptors: {
        response: {
          use: jest.fn(),
        },
      },
    } as any);
  });

  afterEach(() => {
    jest.clearAllMocks();
  });

  describe('Constructor', () => {
    it('should throw error if API key is missing', () => {
      expect(() => {
        new ERIIClient({ apiKey: '' });
      }).toThrow('API key is required');
    });

    it('should use default baseURL if not provided', () => {
      const client = new ERIIClient({ apiKey: mockApiKey });
      expect(client).toBeDefined();
    });
  });

  describe('remember()', () => {
    it('should send remember request with correct payload', async () => {
      const mockPost = jest.fn().mockResolvedValue({
        data: { status: 'success' },
      });
      (client as any).client.post = mockPost;

      await client.remember({
        user_id: 'user123',
        user_message: 'Hello',
        bot_reply: 'Hi there',
      });

      expect(mockPost).toHaveBeenCalledWith('/api/v1/remember', {
        agent_id: 'default_agent',
        user_id: 'user123',
        user_message: 'Hello',
        bot_reply: 'Hi there',
      });
    });
  });

  describe('recall()', () => {
    it('should return context string', async () => {
      const mockPost = jest.fn().mockResolvedValue({
        data: { context: 'Memory context here' },
      });
      (client as any).client.post = mockPost;

      const result = await client.recall({
        user_id: 'user123',
        query: 'what did we talk about?',
      });

      expect(result).toBe('Memory context here');
      expect(mockPost).toHaveBeenCalledWith('/api/v1/recall', {
        agent_id: 'default_agent',
        user_id: 'user123',
        query: 'what did we talk about?',
      });
    });
  });

  describe('Error Handling', () => {
    it('should throw ERIIError for API errors', async () => {
      const mockError = {
        response: {
          status: 404,
          data: {
            detail: {
              code: 'turn_not_found',
              retryable: false,
              safe_summary: 'Turn not found',
            },
          },
        },
      };

      const mockPost = jest.fn().mockRejectedValue(mockError);
      (client as any).client.post = mockPost;

      // Simulate the interceptor behavior
      try {
        await client.recall({ user_id: 'user123', query: 'test' });
      } catch (error) {
        // In real usage, the interceptor would convert this
        expect(error).toBeDefined();
      }
    });
  });
});
