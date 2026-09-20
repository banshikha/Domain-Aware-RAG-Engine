'use strict';

const { validationResult } = require('express-validator');
const { HTTP } = require('../config/constants');

/**
 * Reads express-validator results from the request and returns 422 if any
 * errors exist. Place this after all `check()`/`body()` validator chains
 * in a route definition.
 *
 * Usage:
 *   router.post('/register', [...validators], validate, authController.register)
 */
function validate(req, res, next) {
  const errors = validationResult(req);
  if (errors.isEmpty()) return next();

  const details = errors.array().map(({ path, msg, value }) => ({
    field: path,
    message: msg,
    ...(value !== undefined && value !== null ? { received: String(value).slice(0, 100) } : {}),
  }));

  return res.status(HTTP.UNPROCESSABLE).json({
    success: false,
    error: 'Request validation failed.',
    details,
  });
}

module.exports = validate;
