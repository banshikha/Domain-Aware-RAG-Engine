'use strict';

const rateLimit = require('express-rate-limit');
const config = require('../config');
const { HTTP } = require('../config/constants');

/**
 * Base rate limiter factory.
 * Keyed by user ID when authenticated, IP address otherwise.
 * All state lives in memory for development. Redis store should be added
 * for production multi-instance deployments (see `rate-limit-redis` package).
 */
function createLimiter({ windowMs, max, message }) {
  return rateLimit({
    windowMs,
    max,
    standardHeaders: true,   // Return RateLimit-* headers
    legacyHeaders: false,     // Disable X-RateLimit-* headers
    keyGenerator: (req) => req.user?.id || req.ip,
    handler: (req, res) => {
      req.log?.warn('Rate limit exceeded', {
        userId: req.user?.id,
        ip: req.ip,
        path: req.path,
      });
      res.status(HTTP.TOO_MANY_REQUESTS).json({
        success: false,
        error: message || 'Too many requests. Please slow down.',
        retryAfter: Math.ceil(windowMs / 1000),
      });
    },
    skip: (req) => {
      // Never rate-limit health checks.
      return req.path === '/health';
    },
  });
}

/**
 * Standard limiter — applied globally to all API routes.
 */
const globalLimiter = createLimiter({
  windowMs: config.rateLimit.windowMs,
  max: config.rateLimit.maxRequests,
  message: 'Too many requests. Please wait before retrying.',
});

/**
 * Strict limiter for the ingestion endpoint.
 * Document processing is expensive; a low cap prevents abuse.
 */
const ingestLimiter = createLimiter({
  windowMs: config.rateLimit.windowMs,
  max: config.rateLimit.ingestMax,
  message: 'Document upload limit reached for this window. Please wait.',
});

/**
 * Auth endpoint limiter — mitigates credential stuffing attacks.
 */
const authLimiter = createLimiter({
  windowMs: 15 * 60 * 1000, // 15 minutes
  max: 20,
  message: 'Too many authentication attempts. Please wait 15 minutes.',
});

module.exports = { globalLimiter, ingestLimiter, authLimiter };
