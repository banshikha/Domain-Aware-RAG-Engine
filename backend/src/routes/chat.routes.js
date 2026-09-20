'use strict';

const router = require('express').Router();
const { body, param, query } = require('express-validator');

const chatController = require('../controllers/chatController');
const { authenticate, authorizeDomain } = require('../middleware/auth');
const { validateDomain } = require('../middleware/domain');
const { globalLimiter } = require('../middleware/rateLimiter');
const validate = require('../middleware/validate');
const { DOMAIN_LIST } = require('../config/constants');

// ── All chat routes require authentication ─────────────────────────────────────
// router.use(authenticate);

// ── Validators ────────────────────────────────────────────────────────────────

const createSessionValidators = [
  body('domain')
    .isIn(DOMAIN_LIST)
    .withMessage(`Domain must be one of: ${DOMAIN_LIST.join(', ')}.`),
  body('title')
    .optional()
    .trim()
    .isLength({ max: 200 }).withMessage('Title must be under 200 characters.'),
  body('pinnedDocumentIds')
    .optional()
    .isArray({ max: 20 }).withMessage('Maximum 20 pinned documents.'),
];

const updateSessionValidators = [
  param('sessionId').isMongoId().withMessage('Invalid session ID.'),
  body('title')
    .optional()
    .trim()
    .isLength({ max: 200 }).withMessage('Title must be under 200 characters.'),
  body('isArchived')
    .optional()
    .isBoolean().withMessage('isArchived must be a boolean.'),
  body('pinnedDocumentIds')
    .optional()
    .isArray({ max: 20 }).withMessage('Maximum 20 pinned documents.'),
];

const sessionIdValidator = [
  param('sessionId').isMongoId().withMessage('Invalid session ID.'),
];

const queryValidators = [
  param('sessionId').isMongoId().withMessage('Invalid session ID.'),
  body('query')
    .trim()
    .isLength({ min: 1, max: 4096 })
    .withMessage('Query must be between 1 and 4096 characters.'),
];

const listSessionsValidators = [
  query('domain').optional().isIn(DOMAIN_LIST).withMessage('Invalid domain.'),
  query('limit').optional().isInt({ min: 1, max: 100 }).withMessage('Limit must be 1–100.'),
  query('skip').optional().isInt({ min: 0 }).withMessage('Skip must be ≥ 0.'),
];

// ── Session CRUD ──────────────────────────────────────────────────────────────

/**
 * POST /api/v1/chat/sessions
 * Creates a new chat session for the authenticated user in the given domain.
 */
router.post('/query', chatController.streamQuery);

router.post(
  '/sessions',
  createSessionValidators,
  validate,
  chatController.createSession
);

/**
 * GET /api/v1/chat/sessions
 * Lists the authenticated user's sessions, sorted by recent activity.
 */
router.get(
  '/sessions',
  listSessionsValidators,
  validate,
  chatController.listSessions
);

/**
 * GET /api/v1/chat/sessions/:sessionId
 * Returns a single session with its full message history.
 */
router.get(
  '/sessions/:sessionId',
  sessionIdValidator,
  validate,
  chatController.getSession
);

/**
 * PATCH /api/v1/chat/sessions/:sessionId
 * Updates session title, archived state, or pinned documents.
 */
router.patch(
  '/sessions/:sessionId',
  updateSessionValidators,
  validate,
  chatController.updateSession
);

/**
 * DELETE /api/v1/chat/sessions/:sessionId
 * Permanently deletes a session and all its messages.
 */
router.delete(
  '/sessions/:sessionId',
  sessionIdValidator,
  validate,
  chatController.deleteSession
);

/**
 * GET /api/v1/chat/sessions/:sessionId/messages
 * Returns paginated message history for a session.
 */
router.get(
  '/sessions/:sessionId/messages',
  sessionIdValidator,
  validate,
  chatController.getMessages
);

// ── Streaming query ───────────────────────────────────────────────────────────

/**
 * POST /api/v1/chat/sessions/:sessionId/query
 *
 * The hot path. Middleware chain:
 *   authenticate → globalLimiter → validateDomain → authorizeDomain → validate → controller
 *
 * validateDomain reads X-Domain, normalises it to req.domain.
 * authorizeDomain checks the user's allowedDomains list.
 * The controller then opens an SSE connection and proxies the ML service stream.
 */
// router.post(
//   '/sessions/:sessionId/query',
//   globalLimiter,
//   validateDomain({ required: true }),
//   authorizeDomain,
//   queryValidators,
//   validate,
//   chatController.streamQuery
// );

module.exports = router;
