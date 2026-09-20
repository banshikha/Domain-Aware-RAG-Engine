'use strict';

const router = require('express').Router();
const { body } = require('express-validator');

const authController = require('../controllers/authController');
const { authenticate } = require('../middleware/auth');
const { authLimiter } = require('../middleware/rateLimiter');
const validate = require('../middleware/validate');

// ── Validators ────────────────────────────────────────────────────────────────

const registerValidators = [
  body('email')
    .isEmail().withMessage('Must be a valid email address.')
    .normalizeEmail(),
  body('password')
    .isLength({ min: 8 }).withMessage('Password must be at least 8 characters.')
    .matches(/[A-Z]/).withMessage('Password must contain at least one uppercase letter.')
    .matches(/[0-9]/).withMessage('Password must contain at least one number.'),
  body('name')
    .trim()
    .isLength({ min: 2, max: 100 }).withMessage('Name must be between 2 and 100 characters.'),
];

const loginValidators = [
  body('email').isEmail().withMessage('Must be a valid email address.').normalizeEmail(),
  body('password').notEmpty().withMessage('Password is required.'),
];

const refreshValidators = [
  body('refreshToken').notEmpty().withMessage('Refresh token is required.'),
];

// ── Routes ────────────────────────────────────────────────────────────────────

/**
 * POST /api/v1/auth/register
 * Public. Creates a new user account.
 */
router.post(
  '/register',
  authLimiter,
  registerValidators,
  validate,
  authController.register
);

/**
 * POST /api/v1/auth/login
 * Public. Returns access + refresh tokens on valid credentials.
 */
router.post(
  '/login',
  authLimiter,
  loginValidators,
  validate,
  authController.login
);

/**
 * POST /api/v1/auth/refresh
 * Public. Exchanges a valid refresh token for a new token pair.
 */
router.post(
  '/refresh',
  authLimiter,
  refreshValidators,
  validate,
  authController.refreshToken
);

/**
 * POST /api/v1/auth/logout
 * Authenticated. Revokes the provided refresh token.
 */
router.post(
  '/logout',
  authenticate,
  authController.logout
);

/**
 * GET /api/v1/auth/me
 * Authenticated. Returns the current user's profile.
 */
router.get(
  '/me',
  authenticate,
  authController.getMe
);

module.exports = router;
