'use strict';

const User = require('../models/User');
const { signAccessToken, signRefreshToken, verifyRefreshToken } = require('../services/tokenService');
const { HTTP } = require('../config/constants');

// ── Register ──────────────────────────────────────────────────────────────────

async function register(req, res, next) {
  try {
    const { email, password, name } = req.body;

    const existing = await User.findByEmail(email);
    if (existing) {
      return res.status(HTTP.CONFLICT).json({
        success: false,
        error: 'An account with this email already exists.',
      });
    }

    const user = await User.create({ email, password, name });

    req.log.info('User registered', { userId: user._id });

    return res.status(HTTP.CREATED).json({
      success: true,
      data: { user },
    });
  } catch (err) {
    next(err);
  }
}

// ── Login ──────────────────────────────────────────────────────────────────────

async function login(req, res, next) {
  try {
    const { email, password } = req.body;

    // Load password field explicitly (it is select:false by default).
    const user = await User.findByEmail(email).select('+password +refreshTokens');

    const valid = user && (await user.comparePassword(password));
    if (!valid) {
      req.log.warn('Failed login attempt', { email });
      return res.status(HTTP.UNAUTHORIZED).json({
        success: false,
        error: 'Invalid email or password.',
      });
    }

    const accessToken = signAccessToken(user);
    const refreshToken = signRefreshToken(user);

    await user.addRefreshToken(refreshToken);
    user.lastLoginAt = new Date();
    await user.save();

    req.log.info('User logged in', { userId: user._id });

    return res.status(HTTP.OK).json({
      success: true,
      data: {
        accessToken,
        refreshToken,
        expiresIn: '15m',
        user,
      },
    });
  } catch (err) {
    next(err);
  }
}

// ── Refresh token ─────────────────────────────────────────────────────────────

async function refreshToken(req, res, next) {
  try {
    const { refreshToken: token } = req.body;

    if (!token) {
      return res.status(HTTP.BAD_REQUEST).json({
        success: false,
        error: 'Refresh token is required.',
      });
    }

    let payload;
    try {
      payload = verifyRefreshToken(token);
    } catch {
      return res.status(HTTP.UNAUTHORIZED).json({
        success: false,
        error: 'Invalid or expired refresh token.',
      });
    }

    const user = await User.findById(payload.sub).select('+refreshTokens');

    if (!user || !user.isActive) {
      return res.status(HTTP.UNAUTHORIZED).json({ success: false, error: 'User not found.' });
    }

    // consumeRefreshToken validates the token and removes it (rotation).
    const consumed = await user.consumeRefreshToken(token);
    if (!consumed) {
      // Token was not found — possible replay attack. Revoke all sessions.
      await user.revokeAllTokens();
      req.log.warn('Refresh token reuse detected — all sessions revoked', { userId: user._id });
      return res.status(HTTP.UNAUTHORIZED).json({
        success: false,
        error: 'Refresh token already used. All sessions have been revoked.',
      });
    }

    const newAccessToken = signAccessToken(user);
    const newRefreshToken = signRefreshToken(user);
    await user.addRefreshToken(newRefreshToken);

    req.log.info('Token refreshed', { userId: user._id });

    return res.status(HTTP.OK).json({
      success: true,
      data: { accessToken: newAccessToken, refreshToken: newRefreshToken, expiresIn: '15m' },
    });
  } catch (err) {
    next(err);
  }
}

// ── Logout ────────────────────────────────────────────────────────────────────

async function logout(req, res, next) {
  try {
    const { refreshToken: token } = req.body;

    if (token) {
      const user = await User.findById(req.user.id).select('+refreshTokens');
      if (user) await user.consumeRefreshToken(token);
    }

    req.log.info('User logged out', { userId: req.user.id });

    return res.status(HTTP.OK).json({ success: true, message: 'Logged out successfully.' });
  } catch (err) {
    next(err);
  }
}

// ── Get current user ──────────────────────────────────────────────────────────

async function getMe(req, res, next) {
  try {
    const user = await User.findById(req.user.id).lean();
    if (!user) {
      return res.status(HTTP.NOT_FOUND).json({ success: false, error: 'User not found.' });
    }
    return res.status(HTTP.OK).json({ success: true, data: { user } });
  } catch (err) {
    next(err);
  }
}

module.exports = { register, login, refreshToken, logout, getMe };
