'use strict';

/**
 * Domain identifiers — must match the values expected by the FastAPI ML service.
 */
const DOMAINS = Object.freeze({
  FINANCIAL: 'financial',
  MEDICAL: 'medical',
  GENERAL: 'general',
});

const DOMAIN_LIST = Object.values(DOMAINS);

/**
 * User roles for RBAC.
 * ADMIN  — full access including user management.
 * USER   — standard access: query + upload own documents.
 * VIEWER — read-only: query only, no upload.
 */
const ROLES = Object.freeze({
  ADMIN: 'admin',
  USER: 'user',
  VIEWER: 'viewer',
});

/**
 * Chat message authors.
 */
const MESSAGE_ROLES = Object.freeze({
  USER: 'user',
  ASSISTANT: 'assistant',
  SYSTEM: 'system',
});

/**
 * Document ingestion statuses tracked in MongoDB.
 */
const INGESTION_STATUS = Object.freeze({
  PENDING: 'pending',
  PROCESSING: 'processing',
  COMPLETED: 'completed',
  FAILED: 'failed',
});

/**
 * Header names used for cross-service communication.
 */
const HEADERS = Object.freeze({
  DOMAIN: 'x-domain',
  REQUEST_ID: 'x-request-id',
  USER_ID: 'x-user-id',
  ML_API_KEY: 'x-api-key',
});

/**
 * Redis key prefixes.
 */
const REDIS_KEYS = Object.freeze({
  SEMANTIC_CACHE: 'sem_cache:',
  RATE_LIMIT: 'rl:',
  REFRESH_TOKEN: 'rt:',
  INGEST_JOB: 'ingest_job:',
});

/**
 * Bull queue names.
 */
const QUEUES = Object.freeze({
  DOCUMENT_INGEST: 'document-ingest',
});

/**
 * HTTP status codes used throughout the gateway.
 * Keeps status code intent explicit rather than relying on magic numbers.
 */
const HTTP = Object.freeze({
  OK: 200,
  CREATED: 201,
  NO_CONTENT: 204,
  BAD_REQUEST: 400,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  CONFLICT: 409,
  UNPROCESSABLE: 422,
  TOO_MANY_REQUESTS: 429,
  INTERNAL_ERROR: 500,
  BAD_GATEWAY: 502,
  SERVICE_UNAVAILABLE: 503,
});

module.exports = {
  DOMAINS,
  DOMAIN_LIST,
  ROLES,
  MESSAGE_ROLES,
  INGESTION_STATUS,
  HEADERS,
  REDIS_KEYS,
  QUEUES,
  HTTP,
};
