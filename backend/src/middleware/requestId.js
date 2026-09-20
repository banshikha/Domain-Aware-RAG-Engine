'use strict';

const { v4: uuidv4 } = require('uuid');
const logger = require('../config/logger');
const { HEADERS } = require('../config/constants');

/**
 * Assigns a unique request ID to every incoming request.
 * Propagates an existing ID from the `x-request-id` header when present
 * (e.g. when the gateway sits behind a load balancer that stamps its own IDs).
 * Attaches a scoped child logger to `req.log` so all downstream middleware
 * and controllers log with the same request correlation ID.
 */
function requestId(req, res, next) {
  const id = req.headers[HEADERS.REQUEST_ID] || uuidv4();
  req.requestId = id;
  res.setHeader(HEADERS.REQUEST_ID, id);
  req.log = logger.forRequest(id);
  next();
}

module.exports = requestId;
