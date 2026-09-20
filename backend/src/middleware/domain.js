'use strict';

const { DOMAIN_LIST, HEADERS, HTTP, DOMAINS } = require('../config/constants');

/**
 * Reads the `x-domain` header, validates it against the known domain list,
 * and attaches the normalised value to `req.domain`.
 *
 * Routes that don't require a domain context (e.g. auth, health) skip this.
 * Pass `{ required: false }` to make the header optional.
 */
function validateDomain({ required = true } = {}) {
  return (req, res, next) => {
    const raw = req.headers[HEADERS.DOMAIN];

    if (!raw) {
      if (required) {
        return res.status(HTTP.BAD_REQUEST).json({
          success: false,
          error: `Missing required header: ${HEADERS.DOMAIN}. Valid values: ${DOMAIN_LIST.join(', ')}.`,
        });
      }
      // Default to general when header is absent and not required.
      req.domain = DOMAINS.GENERAL;
      return next();
    }

    const domain = raw.toLowerCase().trim();

    if (!DOMAIN_LIST.includes(domain)) {
      return res.status(HTTP.BAD_REQUEST).json({
        success: false,
        error: `Invalid domain '${raw}'. Valid values: ${DOMAIN_LIST.join(', ')}.`,
      });
    }

    req.domain = domain;
    next();
  };
}

module.exports = { validateDomain };
