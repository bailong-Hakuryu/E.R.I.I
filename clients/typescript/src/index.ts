/**
 * Server-side TypeScript client for the E.R.I.I. reference REST service.
 *
 * The reference service uses one owner API key. Never ship that key in a
 * browser bundle, mobile application, or other untrusted client.
 */

import axios, { AxiosError, AxiosInstance } from 'axios';

export type JSONObject = Record<string, unknown>;
export type MemoryPackData = JSONObject;

export interface ERIIErrorDetail {
  code: string;
  retryable: boolean;
  safe_summary: string;
  receipt?: JSONObject;
}

export class ERIIError extends Error {
  public readonly code: string;
  public readonly retryable: boolean;
  public readonly statusCode: number;
  public readonly receipt?: JSONObject;

  constructor(detail: ERIIErrorDetail, statusCode: number) {
    super(detail.safe_summary);
    this.name = 'ERIIError';
    this.code = detail.code;
    this.retryable = detail.retryable;
    this.statusCode = statusCode;
    this.receipt = detail.receipt;
  }
}

export interface ERIIClientConfig {
  /** Single-owner service key configured by ERII_API_KEY on the server. */
  apiKey: string;
  /** @default "http://localhost:8000" */
  baseURL?: string;
  /** @default 30000 */
  timeout?: number;
}

export interface AgentUserScope {
  agent_id?: string;
  user_id: string;
}

export interface LegacyTurnRecord extends AgentUserScope {
  user_message: string;
  bot_reply: string;
}

/** Backwards-compatible name for the legacy `/remember` request. */
export type TurnRecord = LegacyTurnRecord;

export interface RecallRequest extends AgentUserScope {
  query: string;
  top_k?: number;
}

export interface CoreMemoryRequest extends AgentUserScope {
  content: string;
}

export type APIResponse<T extends object = Record<string, never>> = {
  status: 'success';
} & T;

export interface HealthResponse {
  status: 'healthy';
  version: string;
  engine_initialized: boolean;
  archiver_running: boolean;
}

export type TurnStatus = 'open' | 'completed' | 'abandoned';
export type DeliveryDisposition = 'shown' | 'overridden' | 'shown_unreviewed';
export type SourceProcessingChannel =
  | 'memory_archival'
  | 'relationship_adjudication';
export type SourceProcessingState =
  | 'pending'
  | 'artifacts_committed'
  | 'no_output'
  | 'failed';
export type ReplyAttemptStage =
  | 'generation'
  | 'continuity_evaluation'
  | 'delivery_preparation';

export interface InteractionContextSignalInput {
  signal_id: string;
  source: 'host_observed' | 'core_derived' | 'evaluator_inferred';
  signal_type: string;
  value: string;
  evidence_refs?: string[];
  recorded_at?: string;
  relationship_id?: string | null;
  source_turn_id?: string | null;
  producer_version?: string | null;
}

export interface InteractionContextSignal
  extends Omit<InteractionContextSignalInput, 'evidence_refs' | 'recorded_at'> {
  evidence_refs: string[];
  recorded_at: string;
}

export interface TurnMessage {
  message_id: string;
  role: 'user' | 'agent';
  content: string;
  recorded_at: string;
}

export interface SourceTranscript {
  user_message: TurnMessage;
  agent_message: TurnMessage | null;
}

export interface SourceProcessingPlan {
  channels: SourceProcessingChannel[];
  version: string;
}

export interface SourceProcessingOutcome {
  channel: SourceProcessingChannel;
  state: SourceProcessingState;
  updated_at: string;
}

export interface TurnResource {
  turn_id: string;
  relationship_id: string;
  status: TurnStatus;
  transcript: SourceTranscript;
  interaction_context: InteractionContextSignal[];
  source_revision: string;
  turn_format_version?: string;
  record_version: number;
  opened_at: string;
  context_baseline?: JSONObject | null;
  review_record?: JSONObject | null;
  continuity_assessment?: JSONObject | null;
  delivery_disposition: DeliveryDisposition | null;
  delivery_exception?: JSONObject | null;
  processing_plan: SourceProcessingPlan | null;
  processing_outcomes: SourceProcessingOutcome[];
  completed_at: string | null;
  abandoned_at: string | null;
  abandonment_reason: string | null;
}

export interface SourceTurnReceipt {
  source_turn_id: string;
  relationship_id: string;
  source_revision: string;
  accepted_at: string;
  processing_plan: SourceProcessingPlan;
  processing_outcomes: SourceProcessingOutcome[];
}

export interface BeginTurnRequest extends AgentUserScope {
  user_message: string;
  turn_id?: string;
  interaction_context?: InteractionContextSignalInput[];
}

export interface DeliveryExceptionInput {
  exception_record_version: 'delivery-exception-record/v1';
  disposition: 'overridden' | 'shown_unreviewed';
  actor_kind: 'host_policy' | 'human_operator' | 'data_owner';
  actor_id: string;
  reason_code:
    | 'availability_fallback'
    | 'configured_delivery_policy'
    | 'out_of_band_judgment'
    | 'preexisting_visible_exchange'
    | 'legacy_turn_completion';
  decided_at: string;
  reply_attempt_number: number | null;
}

interface TurnCompletionCommon extends AgentUserScope {
  agent_message: string;
  processing_channels?: SourceProcessingChannel[] | null;
}

export type CompleteTurnRequest = TurnCompletionCommon &
  (
    | {
        delivery_disposition: 'shown';
        continuity_result: ContinuityEvaluationResult;
        continuity_assessment?: never;
        delivery_exception?: never;
      }
    | {
        delivery_disposition: 'overridden';
        continuity_result: ContinuityEvaluationResult;
        continuity_assessment?: never;
        delivery_exception: DeliveryExceptionInput;
      }
    | {
        delivery_disposition?: 'shown_unreviewed';
        continuity_result?: never;
        continuity_assessment?: JSONObject | null;
        delivery_exception: DeliveryExceptionInput;
      }
  );

export interface RecordTurnRequest extends AgentUserScope {
  user_message: string;
  agent_message: string;
  turn_id?: string;
  delivery_disposition?: 'shown_unreviewed';
  delivery_exception: DeliveryExceptionInput;
  processing_channels?: SourceProcessingChannel[] | null;
}

export interface TurnListRequest extends AgentUserScope {
  status?: TurnStatus;
}

export interface AbandonTurnRequest extends AgentUserScope {
  reason: string;
}

export interface ContinuityEvaluationRequest extends AgentUserScope {
  proposed_reply: string;
  persona_context_refs: JSONObject[];
  relationship_context_refs?: JSONObject[];
}

export interface ContinuityEvaluationResult extends JSONObject {
  result_version: string;
  review_binding: JSONObject;
}

export interface ReplyAttemptFailureRequest extends AgentUserScope {
  attempt_number: number;
  stage: ReplyAttemptStage;
  capability_descriptor: string;
  failure_classification: string;
}

export interface ReplyAttemptRecord {
  attempt_id: string;
  relationship_id: string;
  turn_id: string;
  attempt_number: number;
  stage: ReplyAttemptStage;
  capability_descriptor: string;
  failure_classification: string;
  attempted_at: string;
}

export interface ArchivalSubmissionRequest extends AgentUserScope {
  source_turn_id: string;
  idempotency_key: string;
}

export type ArchivalStatus =
  | 'pending'
  | 'processing'
  | 'retry_wait'
  | 'completed'
  | 'failed';
export type ArchivalPhase = 'extraction' | 'commit';

export interface ArchivalArtifactReference {
  kind: 'timeline_entry' | 'memory_node';
  artifact_id: string;
  artifact_fingerprint: string | null;
}

export interface ArchivalReceipt {
  archival_id: string;
  relationship_id: string;
  agent_id: string;
  user_id: string;
  source_turn_id: string;
  source_revision: string;
  status: ArchivalStatus;
  phase: ArchivalPhase;
  extractor_descriptor: JSONObject;
  submitted_at: string;
  updated_at: string;
  extraction_attempts: number;
  commit_attempts: number;
  outcome_code: string | null;
  retryable: boolean | null;
  safe_summary: string | null;
  next_attempt_at: number | null;
  completed_at: string | null;
  artifact_manifest: ArchivalArtifactReference[] | null;
  timeline_count: number | null;
  memory_node_count: number | null;
  retention_state: 'full' | 'compacted';
}

export interface MemoryImportRequest {
  pack_data: MemoryPackData;
  agent_id?: string;
  user_id?: string;
  overwrite?: boolean;
}

function isERIIErrorDetail(value: unknown): value is ERIIErrorDetail {
  if (typeof value !== 'object' || value === null) {
    return false;
  }
  const detail = value as Partial<ERIIErrorDetail>;
  return (
    typeof detail.code === 'string' &&
    typeof detail.retryable === 'boolean' &&
    typeof detail.safe_summary === 'string'
  );
}

function withDefaultAgent<T extends AgentUserScope>(request: T): T & { agent_id: string } {
  return {
    ...request,
    agent_id: request.agent_id ?? 'default_agent',
  };
}

function scopedParams(scope: AgentUserScope): { agent_id: string; user_id: string } {
  return {
    agent_id: scope.agent_id ?? 'default_agent',
    user_id: scope.user_id,
  };
}

function resourcePath(segment: string): string {
  return encodeURIComponent(segment);
}

export class ERIIClient {
  private readonly client: AxiosInstance;

  constructor(config: ERIIClientConfig) {
    if (!config.apiKey || Buffer.byteLength(config.apiKey, 'utf8') < 32) {
      throw new Error('API key must contain at least 32 UTF-8 bytes');
    }

    this.client = axios.create({
      baseURL: config.baseURL ?? 'http://localhost:8000',
      timeout: config.timeout ?? 30000,
      headers: {
        'X-API-Key': config.apiKey,
        'Content-Type': 'application/json',
      },
    });

    this.client.interceptors.response.use(
      (response) => response,
      (error: AxiosError) => {
        const data = error.response?.data;
        if (typeof data === 'object' && data !== null && 'detail' in data) {
          const detail = (data as { detail?: unknown }).detail;
          if (isERIIErrorDetail(detail)) {
            throw new ERIIError(detail, error.response?.status ?? 0);
          }
        }
        throw error;
      }
    );
  }

  /** @deprecated Prefer the explicit Turn Lifecycle methods. */
  async remember(record: LegacyTurnRecord): Promise<APIResponse<{ message: string }>> {
    const response = await this.client.post('/api/v1/remember', withDefaultAgent(record));
    return response.data;
  }

  async recall(request: RecallRequest): Promise<string> {
    const response = await this.client.post('/api/v1/recall', withDefaultAgent(request));
    return response.data.context;
  }

  async setCoreMemory(
    request: CoreMemoryRequest
  ): Promise<APIResponse<{ message: string }>> {
    const response = await this.client.post(
      '/api/v1/core_memory',
      withDefaultAgent(request)
    );
    return response.data;
  }

  async getCoreMemory(agentId: string, userId: string): Promise<string> {
    const response = await this.client.get('/api/v1/core_memory', {
      params: scopedParams({ agent_id: agentId, user_id: userId }),
    });
    return response.data.content;
  }

  async beginTurn(request: BeginTurnRequest): Promise<TurnResource> {
    const response = await this.client.post(
      '/api/v1/turns/open',
      withDefaultAgent(request)
    );
    return response.data.turn;
  }

  async recordTurn(request: RecordTurnRequest): Promise<SourceTurnReceipt> {
    const response = await this.client.post('/api/v1/turns', withDefaultAgent(request));
    return response.data.receipt;
  }

  async completeTurn(
    turnId: string,
    request: CompleteTurnRequest
  ): Promise<SourceTurnReceipt> {
    const response = await this.client.post(
      `/api/v1/turns/${resourcePath(turnId)}/complete`,
      withDefaultAgent(request)
    );
    return response.data.receipt;
  }

  async evaluateTurnContinuity(
    turnId: string,
    request: ContinuityEvaluationRequest
  ): Promise<ContinuityEvaluationResult> {
    const response = await this.client.post(
      `/api/v1/turns/${resourcePath(turnId)}/continuity/evaluate`,
      withDefaultAgent(request)
    );
    return response.data.result;
  }

  async recordReplyAttempt(
    turnId: string,
    request: ReplyAttemptFailureRequest
  ): Promise<ReplyAttemptRecord> {
    const response = await this.client.post(
      `/api/v1/turns/${resourcePath(turnId)}/reply-attempts`,
      withDefaultAgent(request)
    );
    return response.data.attempt;
  }

  async listReplyAttempts(
    turnId: string,
    scope: AgentUserScope
  ): Promise<ReplyAttemptRecord[]> {
    const response = await this.client.get(
      `/api/v1/turns/${resourcePath(turnId)}/reply-attempts`,
      { params: scopedParams(scope) }
    );
    return response.data.attempts;
  }

  async abandonTurn(turnId: string, request: AbandonTurnRequest): Promise<TurnResource> {
    const response = await this.client.post(
      `/api/v1/turns/${resourcePath(turnId)}/abandon`,
      withDefaultAgent(request)
    );
    return response.data.turn;
  }

  async getTurn(turnId: string, scope: AgentUserScope): Promise<TurnResource> {
    const response = await this.client.get(`/api/v1/turns/${resourcePath(turnId)}`, {
      params: scopedParams(scope),
    });
    return response.data.turn;
  }

  async listTurns(request: TurnListRequest): Promise<TurnResource[]> {
    const response = await this.client.get('/api/v1/turns', {
      params: {
        ...scopedParams(request),
        ...(request.status === undefined ? {} : { status: request.status }),
      },
    });
    return response.data.turns;
  }

  async archiveTurn(request: ArchivalSubmissionRequest): Promise<ArchivalReceipt> {
    const response = await this.client.post(
      '/api/v1/archivals',
      withDefaultAgent(request)
    );
    return response.data.receipt;
  }

  async getArchival(
    archivalId: string,
    scope: AgentUserScope
  ): Promise<ArchivalReceipt> {
    const response = await this.client.get(
      `/api/v1/archivals/${resourcePath(archivalId)}`,
      { params: scopedParams(scope) }
    );
    return response.data.receipt;
  }

  async exportMemory(scope: AgentUserScope): Promise<MemoryPackData> {
    const response = await this.client.post(
      '/api/v1/memory/export',
      scopedParams(scope)
    );
    return response.data.pack;
  }

  async importMemory(
    request: MemoryImportRequest
  ): Promise<APIResponse<{ message: string; pack: MemoryPackData }>> {
    const response = await this.client.post('/api/v1/memory/import', request);
    return response.data;
  }

  /** @deprecated Prefer `exportMemory({ agent_id, user_id })`. */
  async export(agentId: string, userId: string): Promise<MemoryPackData> {
    return this.exportMemory({ agent_id: agentId, user_id: userId });
  }

  /** @deprecated Prefer `importMemory({ pack_data, ... })`. */
  async import(
    packData: MemoryPackData,
    agentId?: string,
    userId?: string,
    overwrite = false
  ): Promise<APIResponse<{ message: string; pack: MemoryPackData }>> {
    return this.importMemory({
      pack_data: packData,
      agent_id: agentId,
      user_id: userId,
      overwrite,
    });
  }

  async health(): Promise<HealthResponse> {
    const response = await this.client.get('/api/v1/health');
    return response.data;
  }
}
