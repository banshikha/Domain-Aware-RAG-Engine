'use strict';

require('dotenv').config();

/**
 * Asserts a required env var is present.
 * Crashes loudly at startup rather than silently misbehaving at runtime.
 */
function required(key) {
  const value = process.env[key];
  if (!value) {
    throw new Error(`Missing required environment variable: ${key}`);
  }
  return value;
}

function optional(key, fallback) {
  return process.env[key] ?? fallback;
}

const config = {
  env: optional('NODE_ENV', 'development'),
  isProduction: optional('NODE_ENV', 'development') === 'production',
  isDevelopment: optional('NODE_ENV', 'development') === 'development',

  server: {
    port: parseInt(optional('PORT', '3000'), 10),
  },

  mongo: {
    uri: required('MONGO_URI'),
    poolSize: parseInt(optional('MONGO_POOL_SIZE', '10'), 10),
  },

  redis: {
    host: optional('REDIS_HOST', 'localhost'),
    port: parseInt(optional('REDIS_PORT', '6379'), 10),
    password: optional('REDIS_PASSWORD', undefined),
  },

  jwt: {
    secret: required('JWT_SECRET'),
    expiresIn: optional('JWT_EXPIRES_IN', '15m'),
    refreshSecret: required('JWT_REFRESH_SECRET'),
    refreshExpiresIn: optional('JWT_REFRESH_EXPIRES_IN', '7d'),
  },

  mlService: {
    url: optional('ML_SERVICE_URL', 'http://localhost:8000'),
    timeout: parseInt(optional('ML_SERVICE_TIMEOUT', '120000'), 10),
    apiKey: required('ML_SERVICE_API_KEY'),
  },

  upload: {
    dir: optional('UPLOAD_DIR', 'uploads'),
    maxFileSizeBytes: parseInt(optional('MAX_FILE_SIZE_MB', '50'), 10) * 1024 * 1024,
    allowedMimeTypes: optional(
      'ALLOWED_MIME_TYPES',
      'application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    ).split(','),
  },

  rateLimit: {
    windowMs: parseInt(optional('RATE_LIMIT_WINDOW_MS', '60000'), 10),
    maxRequests: parseInt(optional('RATE_LIMIT_MAX_REQUESTS', '60'), 10),
    ingestMax: parseInt(optional('RATE_LIMIT_INGEST_MAX', '10'), 10),
  },

  logging: {
    level: optional('LOG_LEVEL', 'info'),
    dir: optional('LOG_DIR', 'logs'),
  },
};

module.exports = config;
