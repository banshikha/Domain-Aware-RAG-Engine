'use strict';

const router = require('express').Router();

const authRoutes = require('./auth.routes');
const chatRoutes = require('./chat.routes');
const documentRoutes = require('./document.routes');
const healthRoutes = require('./health.routes');
const ingestRoutes = require('./ingest.routes');

/**
 * Health endpoints sit outside the /api/v1 prefix so that load balancers,
 * Kubernetes probes, and orchestration tooling can reach them at a stable
 * path that won't change if the API version is bumped.
 */
router.use('/health', healthRoutes);

/**
 * All application routes are versioned under /api/v1.
 * Adding /api/v2 in the future means mounting a parallel set of routes
 * here without touching any existing handlers.
 */
router.use('/api/v1/auth', authRoutes);
router.use('/api/v1/chat', chatRoutes);
router.use('/api/v1/documents', documentRoutes);
router.use('/api/v1/ingest', ingestRoutes);

module.exports = router;
