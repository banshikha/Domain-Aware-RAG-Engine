'use strict';

const jwt = require('jsonwebtoken');
const config = require('../config');

/**
 * Issues a short-lived access token containing the user's ID, email, and role.
 * The `sub` claim is the canonical user identifier used by auth middleware.
 */
function signAccessToken(user) {
  return jwt.sign(
    {
      sub: user._id.toString(),
      email: user.email,
      role: user.role,
    },
    config.jwt.secret,
    { expiresIn: config.jwt.expiresIn }
  );
}

/**
 * Issues a long-lived refresh token.
 * Contains only the minimal sub claim — role/email are not embedded
 * to avoid stale data in long-lived tokens.
 */
function signRefreshToken(user) {
  return jwt.sign(
    { sub: user._id.toString() },
    config.jwt.refreshSecret,
    { expiresIn: config.jwt.refreshExpiresIn }
  );
}

/**
 * Verifies and decodes a refresh token.
 * Throws JsonWebTokenError or TokenExpiredError on failure.
 */
function verifyRefreshToken(token) {
  return jwt.verify(token, config.jwt.refreshSecret);
}

/**
 * Returns the expiry of the refresh token as a Date.
 * Used to set cookie maxAge.
 */
function getRefreshTokenExpiry() {
  const decoded = jwt.decode(
    jwt.sign({ sub: 'tmp' }, config.jwt.refreshSecret, { expiresIn: config.jwt.refreshExpiresIn })
  );
  return new Date(decoded.exp * 1000);
}

module.exports = { signAccessToken, signRefreshToken, verifyRefreshToken, getRefreshTokenExpiry };
