'use strict';

const jwt = require('jsonwebtoken');
const config = require('../config');
const { HTTP, ROLES } = require('../config/constants');
const User = require('../models/User');

/**
 * Extracts the Bearer token from the Authorization header.
 */
function extractToken(req) {
  const authHeader = req.headers.authorization;
  if (authHeader && authHeader.startsWith('Bearer ')) {
    return authHeader.slice(7);
  }
  return null;
}

/**
 * Core authentication middleware.
 * Verifies the JWT, loads the user from MongoDB, and attaches it to `req.user`.
 * Fails with 401 if the token is missing, expired, or the user is deactivated.
 */
async function authenticate(req, res, next) {
  const token = extractToken(req);

  if (!token) {
    return res.status(HTTP.UNAUTHORIZED).json({
      success: false,
      error: 'Authentication required. Provide a Bearer token.',
    });
  }

  try {
    const payload = jwt.verify(token, config.jwt.secret);

    // Load user to verify account is still active and fetch current role.
    // Use lean() for a plain object — no Mongoose overhead for auth checks.
    const user = await User.findById(payload.sub).select('role isActive allowedDomains').lean();

    if (!user || !user.isActive) {
      return res.status(HTTP.UNAUTHORIZED).json({
        success: false,
        error: 'User account not found or deactivated.',
      });
    }

    req.user = {
      id: payload.sub,
      email: payload.email,
      role: user.role,
      allowedDomains: user.allowedDomains,
    };

    req.log = req.log.child ? req.log.child({ userId: req.user.id }) : req.log;
    next();
  } catch (err) {
    if (err.name === 'TokenExpiredError') {
      return res.status(HTTP.UNAUTHORIZED).json({
        success: false,
        error: 'Access token expired. Refresh your token.',
        code: 'TOKEN_EXPIRED',
      });
    }
    if (err.name === 'JsonWebTokenError') {
      return res.status(HTTP.UNAUTHORIZED).json({
        success: false,
        error: 'Invalid access token.',
      });
    }
    // Unexpected error — let the global error handler log it.
    next(err);
  }
}

/**
 * RBAC middleware factory.
 * Usage: `router.delete('/users/:id', authenticate, authorize(ROLES.ADMIN), controller)`
 *
 * Accepts one or more roles. The request is allowed if the user holds any of them.
 */
function authorize(...roles) {
  return (req, res, next) => {
    if (!req.user) {
      return res.status(HTTP.UNAUTHORIZED).json({
        success: false,
        error: 'Authentication required.',
      });
    }

    if (!roles.includes(req.user.role)) {
      req.log.warn('Authorization denied', {
        userRole: req.user.role,
        requiredRoles: roles,
        path: req.path,
      });
      return res.status(HTTP.FORBIDDEN).json({
        success: false,
        error: `Access denied. Required role: ${roles.join(' or ')}.`,
      });
    }

    next();
  };
}

/**
 * Domain access guard.
 * Reads the domain from `req.headers['x-domain']` (set by domain middleware)
 * and checks it against the user's `allowedDomains` list.
 * Admins always pass.
 */
function authorizeDomain(req, res, next) {
  if (!req.user) {
    return res.status(HTTP.UNAUTHORIZED).json({ success: false, error: 'Authentication required.' });
  }

  if (req.user.role === ROLES.ADMIN) return next();

  const domain = req.domain; // set by domain validation middleware
  if (!domain) return next(); // no domain constraint on this route

  if (!req.user.allowedDomains.includes(domain)) {
    return res.status(HTTP.FORBIDDEN).json({
      success: false,
      error: `You are not authorised to access the '${domain}' domain.`,
    });
  }

  next();
}

module.exports = { authenticate, authorize, authorizeDomain };
