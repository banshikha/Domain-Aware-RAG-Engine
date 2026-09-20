'use strict';

const winston = require('winston');
const DailyRotateFile = require('winston-daily-rotate-file');
const path = require('path');
const config = require('./index');

const { combine, timestamp, errors, json, colorize, printf } = winston.format;

/**
 * Human-readable format for local development.
 */
const devFormat = combine(
  colorize({ all: true }),
  timestamp({ format: 'HH:mm:ss' }),
  errors({ stack: true }),
  printf(({ level, message, timestamp: ts, requestId, userId, ...meta }) => {
    let line = `${ts} [${level}]`;
    if (requestId) line += ` [${requestId}]`;
    if (userId) line += ` [user:${userId}]`;
    line += ` ${message}`;
    const extras = Object.keys(meta).filter((k) => k !== 'stack');
    if (extras.length) line += ` ${JSON.stringify(meta)}`;
    if (meta.stack) line += `\n${meta.stack}`;
    return line;
  })
);

/**
 * Structured JSON format for production (ingested by log aggregators).
 */
const prodFormat = combine(timestamp(), errors({ stack: true }), json());

const transports = [];

// Always write to stdout — container log collectors pick this up.
transports.push(
  new winston.transports.Console({
    format: config.isDevelopment ? devFormat : prodFormat,
  })
);

// Rotate error log daily in production.
if (config.isProduction) {
  transports.push(
    new DailyRotateFile({
      level: 'error',
      dirname: config.logging.dir,
      filename: 'error-%DATE%.log',
      datePattern: 'YYYY-MM-DD',
      maxFiles: '30d',
      zippedArchive: true,
      format: prodFormat,
    })
  );

  transports.push(
    new DailyRotateFile({
      dirname: config.logging.dir,
      filename: 'combined-%DATE%.log',
      datePattern: 'YYYY-MM-DD',
      maxFiles: '14d',
      zippedArchive: true,
      format: prodFormat,
    })
  );
}

const logger = winston.createLogger({
  level: config.logging.level,
  transports,
  // Prevent unhandled exceptions from swallowing error detail.
  exceptionHandlers: [new winston.transports.Console()],
  rejectionHandlers: [new winston.transports.Console()],
});

/**
 * Returns a child logger pre-populated with request-scoped fields.
 * Use inside middleware/controllers: `req.log.info('message')`.
 */
logger.forRequest = (requestId, userId) =>
  logger.child({ requestId, ...(userId ? { userId } : {}) });

module.exports = logger;
