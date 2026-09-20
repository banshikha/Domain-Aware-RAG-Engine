'use strict';

const axios = require('axios');
const fs = require('fs');
const FormData = require('form-data');
const config = require('../config');
const { HEADERS } = require('../config/constants');
const logger = require('../config/logger');

/**
 * Axios instance pre-configured for ML service communication.
 * responseType is left as default ('json') here; the streaming endpoint
 * overrides it per-call.
 */
const mlClient = axios.create({
  baseURL: config.mlService.url,
  timeout: 120000,
  headers: {
    'Content-Type': 'application/json',
    [HEADERS.ML_API_KEY]: config.mlService.apiKey,
  },
});

// ── Request interceptor — inject tracing headers ───────────────────────────────

mlClient.interceptors.request.use((axiosConfig) => {
  logger.debug('ML service request', {
    method: axiosConfig.method?.toUpperCase(),
    url: axiosConfig.url,
    domain: axiosConfig.headers?.[HEADERS.DOMAIN],
  });
  return axiosConfig;
});

// ── Response interceptor — log latency ────────────────────────────────────────

mlClient.interceptors.request.use((axiosConfig) => {
  axiosConfig.metadata = { startTime: Date.now() };
  return axiosConfig;
});

mlClient.interceptors.response.use(
  (response) => {
    const duration = Date.now() - (response.config.metadata?.startTime || Date.now());
    logger.debug('ML service response', {
      status: response.status,
      durationMs: duration,
      url: response.config.url,
    });
    return response;
  },
  (error) => {
    const duration = Date.now() - (error.config?.metadata?.startTime || Date.now());
    logger.error('ML service error', {
      status: error.response?.status,
      durationMs: duration,
      url: error.config?.url,
      message: error.message,
    });
    return Promise.reject(error);
  }
);

// ── Service methods ────────────────────────────────────────────────────────────

/**
 * Sends a query to the ML service and pipes the streaming response to the
 * Express response object. The ML service must return `text/event-stream`.
 */
async function streamQuery({ query, domain, requestId, userId, history = [], pinnedDocIds = [] }, expressRes) {
  const response = await mlClient.post(
    '/api/v1/query',
    { query, history, pinned_document_ids: pinnedDocIds },
    {
      responseType: 'stream',
      headers: {
        [HEADERS.DOMAIN]: domain,
        [HEADERS.REQUEST_ID]: requestId,
        [HEADERS.USER_ID]: userId,
      },
    }
  );

  // Set SSE headers on the Express response before piping.
  expressRes.setHeader('Content-Type', 'text/event-stream');
  expressRes.setHeader('Cache-Control', 'no-cache');
  expressRes.setHeader('Connection', 'keep-alive');
  expressRes.setHeader('X-Accel-Buffering', 'no'); // disable nginx buffering

  // Pipe ML service stream → Express response.
  response.data.pipe(expressRes);

  return new Promise((resolve, reject) => {
    response.data.on('end', resolve);
    response.data.on('error', reject);
    // Handle client disconnect — stop reading from upstream.
    expressRes.on('close', () => response.data.destroy());
  });
}

/**
 * Triggers document ingestion in the ML service after the gateway has
 * stored the file and created the Document record.
 */
async function triggerIngestion({ documentId, storagePath, domain, mimeType, requestId, userId }) {
  // Create a multipart/form-data payload
  const form = new FormData();
  
  // Attach the actual file stream (must match the "document" alias in Python)
  form.append('document', fs.createReadStream(storagePath));
  
  // Attach the metadata
  form.append('documentId', documentId);
  form.append('domain', domain || 'general');

  const response = await mlClient.post(
    '/api/v1/ingest',
    form,
    {
      headers: {
        // Automatically injects the correct boundary headers for multipart forms
        ...form.getHeaders(), 
        [HEADERS.DOMAIN]: domain,
        [HEADERS.REQUEST_ID]: requestId,
        [HEADERS.USER_ID]: userId,
      },
    }
  );
  return response.data;
}

/**
 * Requests deletion of a document's vector chunks from the ML service.
 */
async function deleteDocumentVectors({ documentId, domain, requestId }) {
  const response = await mlClient.delete(`/api/v1/documents/${documentId}`, {
    headers: {
      [HEADERS.DOMAIN]: domain,
      [HEADERS.REQUEST_ID]: requestId,
    },
  });
  return response.data;
}

/**
 * Health check against the ML service.
 */
async function checkHealth() {
  const response = await mlClient.get('/health', { timeout: 5000 });
  return response.data;
}

module.exports = { streamQuery, triggerIngestion, deleteDocumentVectors, checkHealth };