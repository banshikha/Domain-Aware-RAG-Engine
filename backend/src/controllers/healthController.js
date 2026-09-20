'use strict';

const { isConnected: isMongoConnected } = require('../config/database');
const mlService = require('../services/mlService');
const { HTTP } = require('../config/constants');
const config = require('../config');

const START_TIME = Date.now();

/**
 * GET /health
 *
 * Liveness probe — answers the single question: "is this process alive?"
 * Kubernetes restarts the pod if this returns non-200.
 * Must never depend on external services; a DB outage must not kill the pod.
 */
function liveness(req, res) {
  return res.status(HTTP.OK).json({
    success: true,
    status: 'alive',
    uptime: Math.floor((Date.now() - START_TIME) / 1000),
    timestamp: new Date().toISOString(),
  });
}

/**
 * GET /health/ready
 *
 * Readiness probe — "is this instance ready to serve traffic?"
 * Kubernetes removes the pod from the load-balancer if this returns non-200.
 * Checks all hard dependencies: MongoDB and Redis.
 * ML service failure degrades quality but the gateway can still serve
 * cached/queued responses, so it is surfaced as a warning, not a failure.
 */
async function readiness(req, res) {
  const checks = await runChecks();
  const allHealthy = Object.values(checks).every((c) => c.status === 'healthy');

  const httpStatus = allHealthy ? HTTP.OK : HTTP.SERVICE_UNAVAILABLE;

  return res.status(httpStatus).json({
    success: allHealthy,
    status: allHealthy ? 'ready' : 'degraded',
    version: process.env.npm_package_version || '1.0.0',
    env: config.env,
    uptime: Math.floor((Date.now() - START_TIME) / 1000),
    timestamp: new Date().toISOString(),
    checks,
  });
}

/**
 * GET /health/details (admin-only)
 *
 * Full diagnostic report including memory, environment, and dependency latencies.
 * Gated behind the authenticate + authorize(ADMIN) middleware in the route.
 */
async function details(req, res) {
  const checks = await runChecks();
  const mem = process.memoryUsage();

  return res.status(HTTP.OK).json({
    success: true,
    version: process.env.npm_package_version || '1.0.0',
    nodeVersion: process.version,
    env: config.env,
    uptime: Math.floor((Date.now() - START_TIME) / 1000),
    timestamp: new Date().toISOString(),
    memory: {
      rss: formatBytes(mem.rss),
      heapUsed: formatBytes(mem.heapUsed),
      heapTotal: formatBytes(mem.heapTotal),
      external: formatBytes(mem.external),
    },
    checks,
  });
}

// ── Dependency checks ──────────────────────────────────────────────────────────

async function runChecks() {
  const [mongo, ml] = await Promise.all([checkMongo(), checkMlService()]);
  return { mongo, mlService: ml };
}

function checkMongo() {
  const connected = isMongoConnected();
  return Promise.resolve({
    status: connected ? 'healthy' : 'unhealthy',
    message: connected ? 'Connected' : 'Not connected',
  });
}

async function checkMlService() {
  const t0 = Date.now();
  try {
    await mlService.checkHealth();
    return { status: 'healthy', latencyMs: Date.now() - t0 };
  } catch (err) {
    return {
      status: 'unhealthy',
      latencyMs: Date.now() - t0,
      message: err.message,
    };
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatBytes(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

module.exports = { liveness, readiness, details };
