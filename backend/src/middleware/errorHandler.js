'use strict';

const mongoose = require('mongoose');
const { HTTP } = require('../config/constants');
const logger = require('../config/logger');
const config = require('../config');

/**
 * Converts a raw error into a normalised { status, message, details } shape.
 */
function normaliseError(err) {
  // Mongoose validation error (e.g. required field missing)
  if (err instanceof mongoose.Error.ValidationError) {
    const details = Object.values(err.errors).map((e) => ({
      field: e.path,
      message: e.message,
    }));
    return { status: HTTP.UNPROCESSABLE, message: 'Validation failed.', details };
  }

  // Mongoose duplicate key (e.g. duplicate email)
  if (err.code === 11000) {
    const field = Object.keys(err.keyPattern || {})[0] || 'field';
    return {
      status: HTTP.CONFLICT,
      message: `A record with this ${field} already exists.`,
    };
  }

  // Mongoose cast error (e.g. invalid ObjectId in URL param)
  if (err instanceof mongoose.Error.CastError) {
    return { status: HTTP.BAD_REQUEST, message: `Invalid value for field '${err.path}'.` };
  }

  // Axios error from ML service proxy
  if (err.isAxiosError) {
    if (err.code === 'ECONNREFUSED' || err.code === 'ENOTFOUND') {
      return { status: HTTP.SERVICE_UNAVAILABLE, message: 'ML service is currently unreachable.' };
    }
    if (err.code === 'ETIMEDOUT' || err.code === 'ECONNABORTED') {
      return { status: HTTP.SERVICE_UNAVAILABLE, message: 'ML service request timed out.' };
    }
    const upstream = err.response?.data?.detail || err.response?.data?.error;
    return {
      status: err.response?.status || HTTP.BAD_GATEWAY,
      message: upstream || 'Unexpected error from ML service.',
    };
  }

  // Errors thrown with an explicit status (e.g. `createHttpError(404)`)
  if (err.status && Number.isInteger(err.status)) {
    return { status: err.status, message: err.message };
  }

  // Default: unexpected server error
  return { status: HTTP.INTERNAL_ERROR, message: 'An unexpected error occurred.' };
}

/**
 * Express 4-argument error handling middleware.
 * Must be registered AFTER all routes.
 */
// eslint-disable-next-line no-unused-vars
function errorHandler(err, req, res, next) {
  const { status, message, details } = normaliseError(err);

  const isServerError = status >= 500;

  // Log server errors with full stack; client errors at warn level only.
  if (isServerError) {
    (req.log || logger).error('Unhandled error', {
      message: err.message,
      stack: err.stack,
      path: req.path,
      method: req.method,
    });
  } else {
    (req.log || logger).warn('Client error', { message, path: req.path, status });
  }

  const body = {
    success: false,
    error: message,
    ...(details ? { details } : {}),
    // Expose stack trace in development only — never in production.
    ...(config.isDevelopment && isServerError ? { stack: err.stack } : {}),
  };

  res.status(status).json(body);
}

/**
 * Catch-all for routes that don't exist.
 */
function notFound(req, res) {
  res.status(HTTP.NOT_FOUND).json({
    success: false,
    error: `Route not found: ${req.method} ${req.originalUrl}`,
  });
}

module.exports = { errorHandler, notFound };
