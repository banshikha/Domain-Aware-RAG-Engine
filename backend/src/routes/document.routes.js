'use strict';

const router = require('express').Router();
const { param, query } = require('express-validator');

const documentController = require('../controllers/documentController');
const { authenticate, authorize, authorizeDomain } = require('../middleware/auth');
const { validateDomain } = require('../middleware/domain');
const { ingestLimiter, globalLimiter } = require('../middleware/rateLimiter');
const { uploadSingle } = require('../middleware/upload');
const validate = require('../middleware/validate');
const { ROLES, DOMAIN_LIST, INGESTION_STATUS } = require('../config/constants');

// ── Internal API key check (webhook only) ─────────────────────────────────────

/**
 * Lightweight middleware that validates the internal ML_SERVICE_API_KEY.
 * Only applied to the webhook route — not exposed to end users.
 */
function internalApiKey(req, res, next) {
  const key = req.headers['x-api-key'];
  if (!key || key !== process.env.ML_SERVICE_API_KEY) {
    return res.status(401).json({ success: false, error: 'Unauthorized.' });
  }
  next();
}

// ── Validators ────────────────────────────────────────────────────────────────

const documentIdValidator = [
  param('documentId').isMongoId().withMessage('Invalid document ID.'),
];

const listDocumentsValidators = [
  query('domain').optional().isIn(DOMAIN_LIST).withMessage('Invalid domain.'),
  query('status')
    .optional()
    .isIn(Object.values(INGESTION_STATUS))
    .withMessage('Invalid status value.'),
  query('limit').optional().isInt({ min: 1, max: 100 }).withMessage('Limit must be 1–100.'),
  query('skip').optional().isInt({ min: 0 }).withMessage('Skip must be ≥ 0.'),
];

// ── Routes ────────────────────────────────────────────────────────────────────

/**
 * POST /api/v1/documents
 *
 * Upload a document for ingestion. Middleware chain:
 *   authenticate → ingestLimiter → uploadSingle (multer) →
 *   validateDomain → authorizeDomain → controller
 *
 * The file is written to disk by multer before the controller runs.
 * uploadSingle is an array [multer handler, multer error handler].
 */
router.post(
  '/',
  authenticate,
  ingestLimiter,
  ...uploadSingle,
  validateDomain({ required: true }),
  authorizeDomain,
  documentController.uploadDocument
);

/**
 * GET /api/v1/documents
 * Lists the authenticated user's documents with optional filters.
 */
router.get(
  '/',
  authenticate,
  globalLimiter,
  listDocumentsValidators,
  validate,
  documentController.listDocuments
);

/**
 * GET /api/v1/documents/:documentId
 * Returns a single document record.
 */
router.get(
  '/:documentId',
  authenticate,
  documentIdValidator,
  validate,
  documentController.getDocument
);

/**
 * GET /api/v1/documents/:documentId/status
 * Lightweight status-only endpoint for frontend polling.
 * Returns { status, chunkCount, updatedAt } — avoids loading the full record.
 */
router.get(
  '/:documentId/status',
  authenticate,
  documentIdValidator,
  validate,
  documentController.getIngestionStatus
);

/**
 * DELETE /api/v1/documents/:documentId
 * Soft-deletes the record, removes the file, and triggers ML vector cleanup.
 */
router.delete(
  '/:documentId',
  authenticate,
  documentIdValidator,
  validate,
  documentController.deleteDocument
);

/**
 * POST /api/v1/documents/webhook/ingest
 *
 * Internal endpoint — called by the ML service when ingestion completes.
 * Protected by the internal API key, NOT by JWT (the ML service has no user token).
 *
 * Must be defined BEFORE /:documentId to avoid being swallowed by that route.
 */
router.post(
  '/webhook/ingest',
  internalApiKey,
  documentController.ingestWebhook
);

/**
 * GET /api/v1/documents/admin/all
 * Admin-only. Lists all documents across all users with optional filters.
 */
router.get(
  '/admin/all',
  authenticate,
  authorize(ROLES.ADMIN),
  listDocumentsValidators,
  validate,
  documentController.adminListDocuments
);

module.exports = router;
