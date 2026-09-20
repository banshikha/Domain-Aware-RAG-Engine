'use strict';

const router = require('express').Router();

const healthController = require('../controllers/healthController');
const { authenticate, authorize } = require('../middleware/auth');
const { ROLES } = require('../config/constants');

/**
 * GET /health
 * Liveness probe — no auth, no rate limiting.
 * Must always respond 200 as long as the Node process is alive.
 */
router.get('/', healthController.liveness);

/**
 * GET /health/ready
 * Readiness probe — checks MongoDB and ML service reachability.
 * No auth — load balancers and orchestrators call this without credentials.
 * Returns 503 if any hard dependency is unhealthy.
 */
router.get('/ready', healthController.readiness);

/**
 * GET /health/details
 * Full diagnostic report: memory usage, dependency latencies, version info.
 * Restricted to admins — exposes internal topology details.
 */
router.get(
  '/details',
  authenticate,
  authorize(ROLES.ADMIN),
  healthController.details
);

module.exports = router;
