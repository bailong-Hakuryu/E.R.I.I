import axios, { AxiosError, AxiosInstance } from 'axios';

import {
  ContinuityEvaluationResult,
  DeliveryExceptionInput,
  ERIIClient,
  ERIIError,
  MemoryPackData,
} from './index';

jest.mock('axios');

const mockedAxios = axios as jest.Mocked<typeof axios>;
const TEST_API_KEY = Array(8).fill('sdk-fixture').join('-');
const UNREVIEWED_EXCEPTION: DeliveryExceptionInput = {
  exception_record_version: 'delivery-exception-record/v1',
  disposition: 'shown_unreviewed',
  actor_kind: 'host_policy',
  actor_id: 'tests.typescript-client/v1',
  reason_code: 'availability_fallback',
  decided_at: '2026-08-11T00:00:00+00:00',
  reply_attempt_number: 1,
};
const PREEXISTING_EXCEPTION: DeliveryExceptionInput = {
  ...UNREVIEWED_EXCEPTION,
  reason_code: 'preexisting_visible_exchange',
};

describe('ERIIClient', () => {
  let client: ERIIClient;
  let get: jest.Mock;
  let post: jest.Mock;
  let responseErrorHandler: (error: AxiosError) => never;

  beforeEach(() => {
    jest.resetAllMocks();
    get = jest.fn();
    post = jest.fn();
    const use = jest.fn((_fulfilled, rejected) => {
      responseErrorHandler = rejected;
      return 0;
    });
    const instance = {
      get,
      post,
      interceptors: { response: { use } },
    } as unknown as AxiosInstance;
    mockedAxios.create.mockReturnValue(instance);

    client = new ERIIClient({
      apiKey: TEST_API_KEY,
      baseURL: 'http://erii.test',
      timeout: 1234,
    });
  });

  it('configures the single-owner X-API-Key contract', () => {
    expect(mockedAxios.create).toHaveBeenCalledWith({
      baseURL: 'http://erii.test',
      timeout: 1234,
      headers: {
        'X-API-Key': TEST_API_KEY,
        'Content-Type': 'application/json',
      },
    });
  });

  it('uses documented defaults and rejects API keys below the server minimum', () => {
    expect(() => new ERIIClient({ apiKey: '' })).toThrow(
      'API key must contain at least 32 UTF-8 bytes'
    );
    expect(() => new ERIIClient({ apiKey: 'short-fixture-key' })).toThrow(
      'API key must contain at least 32 UTF-8 bytes'
    );

    new ERIIClient({ apiKey: `${TEST_API_KEY}-second` });
    expect(mockedAxios.create).toHaveBeenLastCalledWith(
      expect.objectContaining({
        baseURL: 'http://localhost:8000',
        timeout: 30000,
      })
    );
  });

  it('calls the legacy memory, recall, core-memory, and health contracts', async () => {
    post
      .mockResolvedValueOnce({ data: { status: 'success', message: 'accepted' } })
      .mockResolvedValueOnce({ data: { status: 'success', context: 'memory context' } })
      .mockResolvedValueOnce({ data: { status: 'success', message: 'saved' } });
    get
      .mockResolvedValueOnce({ data: { status: 'success', content: 'core' } })
      .mockResolvedValueOnce({
        data: {
          status: 'healthy',
          version: '0.5.0a3',
          engine_initialized: true,
          archiver_running: false,
        },
      });

    await client.remember({
      user_id: 'user-1',
      user_message: 'hello',
      bot_reply: 'hi',
    });
    await expect(
      client.recall({
        agent_id: 'agent-1',
        user_id: 'user-1',
        query: 'hello',
        top_k: 7,
      })
    ).resolves.toBe('memory context');
    await client.setCoreMemory({ user_id: 'user-1', content: 'core' });
    await expect(client.getCoreMemory('agent-1', 'user-1')).resolves.toBe('core');
    await expect(client.health()).resolves.toEqual(
      expect.objectContaining({ status: 'healthy' })
    );

    expect(post).toHaveBeenNthCalledWith(1, '/api/v1/remember', {
      agent_id: 'default_agent',
      user_id: 'user-1',
      user_message: 'hello',
      bot_reply: 'hi',
    });
    expect(post).toHaveBeenNthCalledWith(2, '/api/v1/recall', {
      agent_id: 'agent-1',
      user_id: 'user-1',
      query: 'hello',
      top_k: 7,
    });
    expect(post).toHaveBeenNthCalledWith(3, '/api/v1/core_memory', {
      agent_id: 'default_agent',
      user_id: 'user-1',
      content: 'core',
    });
    expect(get).toHaveBeenNthCalledWith(1, '/api/v1/core_memory', {
      params: { agent_id: 'agent-1', user_id: 'user-1' },
    });
    expect(get).toHaveBeenNthCalledWith(2, '/api/v1/health');
  });

  it('opens, reads, lists, completes, and abandons durable turns', async () => {
    const openTurn = { turn_id: 'turn one', status: 'open' };
    const completedReceipt = { source_turn_id: 'turn one' };
    const abandonedTurn = { turn_id: 'turn-two', status: 'abandoned' };
    post
      .mockResolvedValueOnce({ data: { status: 'success', turn: openTurn } })
      .mockResolvedValueOnce({ data: { status: 'success', receipt: completedReceipt } })
      .mockResolvedValueOnce({ data: { status: 'success', turn: abandonedTurn } });
    get
      .mockResolvedValueOnce({ data: { status: 'success', turn: openTurn } })
      .mockResolvedValueOnce({ data: { status: 'success', turns: [openTurn] } })
      .mockResolvedValueOnce({ data: { status: 'success', turns: [openTurn] } });

    await expect(
      client.beginTurn({
        agent_id: 'agent-1',
        user_id: 'user-1',
        turn_id: 'turn one',
        user_message: 'Are you here?',
        interaction_context: [],
      })
    ).resolves.toBe(openTurn);
    await expect(
      client.completeTurn('turn one', {
        user_id: 'user-1',
        agent_message: 'I am here.',
        delivery_exception: UNREVIEWED_EXCEPTION,
        processing_channels: [],
      })
    ).resolves.toBe(completedReceipt);
    await expect(
      client.abandonTurn('turn-two', {
        user_id: 'user-1',
        reason: 'host closed the interaction',
      })
    ).resolves.toBe(abandonedTurn);
    await expect(
      client.getTurn('turn one', { user_id: 'user-1' })
    ).resolves.toBe(openTurn);
    await client.listTurns({
      agent_id: 'agent-1',
      user_id: 'user-1',
      status: 'open',
    });
    await client.listTurns({ user_id: 'user-1' });

    expect(post).toHaveBeenNthCalledWith(1, '/api/v1/turns/open', {
      agent_id: 'agent-1',
      user_id: 'user-1',
      turn_id: 'turn one',
      user_message: 'Are you here?',
      interaction_context: [],
    });
    expect(post).toHaveBeenNthCalledWith(2, '/api/v1/turns/turn%20one/complete', {
      agent_id: 'default_agent',
      user_id: 'user-1',
      agent_message: 'I am here.',
      delivery_exception: UNREVIEWED_EXCEPTION,
      processing_channels: [],
    });
    expect(post).toHaveBeenNthCalledWith(3, '/api/v1/turns/turn-two/abandon', {
      agent_id: 'default_agent',
      user_id: 'user-1',
      reason: 'host closed the interaction',
    });
    expect(get).toHaveBeenNthCalledWith(1, '/api/v1/turns/turn%20one', {
      params: { agent_id: 'default_agent', user_id: 'user-1' },
    });
    expect(get).toHaveBeenNthCalledWith(2, '/api/v1/turns', {
      params: { agent_id: 'agent-1', user_id: 'user-1', status: 'open' },
    });
    expect(get).toHaveBeenNthCalledWith(3, '/api/v1/turns', {
      params: { agent_id: 'default_agent', user_id: 'user-1' },
    });
  });

  it('records complete turns and supports continuity review', async () => {
    const receipt = { source_turn_id: 'turn-recorded' };
    const result: ContinuityEvaluationResult = {
      result_version: 'continuity-evaluation-result/v1',
      review_binding: { turn_id: 'turn-reviewed' },
    };
    post
      .mockResolvedValueOnce({ data: { status: 'success', receipt } })
      .mockResolvedValueOnce({ data: { status: 'success', result } })
      .mockResolvedValueOnce({ data: { status: 'success', receipt } });

    await expect(
      client.recordTurn({
        user_id: 'user-1',
        user_message: 'hello',
        agent_message: 'hi',
        delivery_exception: PREEXISTING_EXCEPTION,
      })
    ).resolves.toBe(receipt);
    await expect(
      client.evaluateTurnContinuity('turn-reviewed', {
        user_id: 'user-1',
        proposed_reply: 'I am here.',
        persona_context_refs: [{ ref_id: 'persona-1' }],
      })
    ).resolves.toBe(result);
    await client.completeTurn('turn-reviewed', {
      user_id: 'user-1',
      agent_message: 'I am here.',
      delivery_disposition: 'shown',
      continuity_result: result,
      processing_channels: ['memory_archival'],
    });

    expect(post).toHaveBeenNthCalledWith(1, '/api/v1/turns', {
      agent_id: 'default_agent',
      user_id: 'user-1',
      user_message: 'hello',
      agent_message: 'hi',
      delivery_exception: PREEXISTING_EXCEPTION,
    });
    expect(post).toHaveBeenNthCalledWith(
      2,
      '/api/v1/turns/turn-reviewed/continuity/evaluate',
      {
        agent_id: 'default_agent',
        user_id: 'user-1',
        proposed_reply: 'I am here.',
        persona_context_refs: [{ ref_id: 'persona-1' }],
      }
    );
    expect(post).toHaveBeenNthCalledWith(
      3,
      '/api/v1/turns/turn-reviewed/complete',
      {
        agent_id: 'default_agent',
        user_id: 'user-1',
        agent_message: 'I am here.',
        delivery_disposition: 'shown',
        continuity_result: result,
        processing_channels: ['memory_archival'],
      }
    );
  });

  it('records and lists sanitized reply attempts', async () => {
    const attempt = {
      attempt_id: 'attempt-1',
      relationship_id: 'relationship-1',
      turn_id: 'turn-1',
      attempt_number: 1,
      stage: 'generation',
      capability_descriptor: 'provider/model',
      failure_classification: 'temporary_provider_error',
      attempted_at: '2026-08-11T00:00:00+00:00',
    };
    post.mockResolvedValueOnce({ data: { status: 'success', attempt } });
    get.mockResolvedValueOnce({ data: { status: 'success', attempts: [attempt] } });

    await expect(
      client.recordReplyAttempt('turn-1', {
        user_id: 'user-1',
        attempt_number: 1,
        stage: 'generation',
        capability_descriptor: 'provider/model',
        failure_classification: 'temporary_provider_error',
      })
    ).resolves.toBe(attempt);
    await expect(
      client.listReplyAttempts('turn-1', { user_id: 'user-1' })
    ).resolves.toEqual([attempt]);

    expect(post).toHaveBeenCalledWith('/api/v1/turns/turn-1/reply-attempts', {
      agent_id: 'default_agent',
      user_id: 'user-1',
      attempt_number: 1,
      stage: 'generation',
      capability_descriptor: 'provider/model',
      failure_classification: 'temporary_provider_error',
    });
    expect(get).toHaveBeenCalledWith('/api/v1/turns/turn-1/reply-attempts', {
      params: { agent_id: 'default_agent', user_id: 'user-1' },
    });
  });

  it('submits and reads reliable archivals', async () => {
    const receipt = { archival_id: 'archive one', status: 'pending' };
    post.mockResolvedValueOnce({ data: { receipt } });
    get.mockResolvedValueOnce({ data: { receipt } });

    await expect(
      client.archiveTurn({
        user_id: 'user-1',
        source_turn_id: 'turn-1',
        idempotency_key: 'archive-turn-1',
      })
    ).resolves.toBe(receipt);
    await expect(
      client.getArchival('archive one', { agent_id: 'agent-1', user_id: 'user-1' })
    ).resolves.toBe(receipt);

    expect(post).toHaveBeenCalledWith('/api/v1/archivals', {
      agent_id: 'default_agent',
      user_id: 'user-1',
      source_turn_id: 'turn-1',
      idempotency_key: 'archive-turn-1',
    });
    expect(get).toHaveBeenCalledWith('/api/v1/archivals/archive%20one', {
      params: { agent_id: 'agent-1', user_id: 'user-1' },
    });
  });

  it('uses the canonical MemoryPack export/import paths and pack field', async () => {
    const pack: MemoryPackData = { pack_version: '0.5.0a3' };
    const imported = { status: 'success', message: 'imported', pack };
    post
      .mockResolvedValueOnce({ data: { status: 'success', pack } })
      .mockResolvedValueOnce({ data: imported })
      .mockResolvedValueOnce({ data: { status: 'success', pack } })
      .mockResolvedValueOnce({ data: imported });

    await expect(
      client.exportMemory({ agent_id: 'agent-1', user_id: 'user-1' })
    ).resolves.toBe(pack);
    await expect(
      client.importMemory({
        pack_data: pack,
        agent_id: 'agent-2',
        user_id: 'user-2',
        overwrite: true,
      })
    ).resolves.toBe(imported);
    await expect(client.export('agent-1', 'user-1')).resolves.toBe(pack);
    await expect(client.import(pack, 'agent-2', 'user-2', true)).resolves.toBe(
      imported
    );

    expect(post).toHaveBeenNthCalledWith(1, '/api/v1/memory/export', {
      agent_id: 'agent-1',
      user_id: 'user-1',
    });
    expect(post).toHaveBeenNthCalledWith(2, '/api/v1/memory/import', {
      pack_data: pack,
      agent_id: 'agent-2',
      user_id: 'user-2',
      overwrite: true,
    });
  });

  it('turns structured server failures into ERIIError', () => {
    const axiosError = {
      response: {
        status: 503,
        data: {
          detail: {
            code: 'archival_capability_unavailable',
            retryable: false,
            safe_summary: 'reliable archival is not configured',
            receipt: { archival_id: 'archive-1' },
          },
        },
      },
    } as AxiosError;

    try {
      responseErrorHandler(axiosError);
      throw new Error('expected handler to throw');
    } catch (error) {
      expect(error).toBeInstanceOf(ERIIError);
      expect(error).toMatchObject({
        code: 'archival_capability_unavailable',
        retryable: false,
        statusCode: 503,
        message: 'reliable archival is not configured',
        receipt: { archival_id: 'archive-1' },
      });
    }
  });

  it('preserves non-standard Axios failures', () => {
    const axiosError = new AxiosError('network error');
    expect(() => responseErrorHandler(axiosError)).toThrow(axiosError);

    const validationError = {
      response: { status: 422, data: { detail: [] } },
    } as unknown as AxiosError;
    let caught: unknown;
    try {
      responseErrorHandler(validationError);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBe(validationError);
  });
});
