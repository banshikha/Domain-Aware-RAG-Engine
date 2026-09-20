'use strict';

const express = require('express');
const helmet = require('helmet');
let cors = require('cors');
const compression = require('compression');
const morgan = require('morgan');

const config = require('./config');
const logger = require('./config/logger');
const { connectDB, disconnectDB } = require('./config/database');
const routes = require('./routes');
const requestId = require('./middleware/requestId');
const { globalLimiter } = require('./middleware/rateLimiter');
const { errorHandler, notFound } = require('./middleware/errorHandler');

// ── App factory ────────────────────────────────────────────────────────────────

function createApp() {
  const app = express();

  // ── Security headers ────────────────────────────────────────────────────────
  // Helmet sets a sensible default set of HTTP security headers.
  app.use(
    helmet({
      crossOriginEmbedderPolicy: false, // SSE requires this off
      contentSecurityPolicy: config.isProduction
        ? undefined // use helmet defaults in production
        : false,    // disable CSP in development for easier tooling
    })
  );

  // ── CORS ────────────────────────────────────────────────────────────────────
  // In production, restrict origins to the React app's domain.
  // In development, allow all origins for local testing convenience.
  const corsOptions = {
    origin: config.isProduction
      ? (process.env.ALLOWED_ORIGINS || '').split(',').map((o) => o.trim())
      : true,
    credentials: true,
    methods: ['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
    allowedHeaders: [
      'Content-Type',
      'Authorization',
      'X-Domain',
      'X-Request-Id',
      'X-Api-Key',
    ],
    // Expose the request-ID header so the React client can log it.
    exposedHeaders: ['X-Request-Id'],
  };
  app.use(cors(corsOptions));

  // ── Request ID — must be before all other middleware so req.log is available ─
  app.use(requestId);

  // ── HTTP request logging ────────────────────────────────────────────────────
  // In production, pipe morgan output through Winston so all logs share the
  // same structured format and transport.
  app.use(
    morgan(config.isDevelopment ? 'dev' : 'combined', {
      stream: { write: (msg) => logger.http(msg.trimEnd()) },
      // Skip health check noise in production logs.
      skip: (req) => config.isProduction && req.path === '/health',
    })
  );

  // ── Body parsing ────────────────────────────────────────────────────────────
  app.use(express.json({ limit: '1mb' }));
  app.use(express.urlencoded({ extended: true, limit: '1mb' }));

  // ── Compression ─────────────────────────────────────────────────────────────
  // Exclude SSE responses from compression — compressing a stream introduces
  // buffering that defeats the low-latency purpose of SSE.
  app.use(
    compression({
      filter: (req, res) => {
        if (res.getHeader('Content-Type')?.includes('text/event-stream')) return false;
        return compression.filter(req, res);
      },
    })
  );

  // ── Trust proxy ─────────────────────────────────────────────────────────────
  // Required when the gateway sits behind a reverse proxy (nginx, ALB).
  // Allows express-rate-limit to read the real client IP from X-Forwarded-For.
  if (config.isProduction) {
    app.set('trust proxy', 1);
  }

  // ── Global rate limiter ─────────────────────────────────────────────────────
  // Applied before routes so every endpoint is covered.
  // Auth and ingest routes apply additional stricter limiters on top.
  app.use('/api', globalLimiter);

  // ── Routes ──────────────────────────────────────────────────────────────────
  app.use(routes);

  // ── 404 handler ─────────────────────────────────────────────────────────────
  // Must come after all routes.
  app.use(notFound);

  // ── Global error handler ────────────────────────────────────────────────────
  // Must be the last `app.use` call — Express identifies it by the 4-arg sig.
  app.use(errorHandler);

  return app;
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

async function bootstrap() {
  // Connect to MongoDB before accepting traffic.
  await connectDB();

  const app = createApp();

  const server = app.listen(config.server.port, () => {
    logger.info(`API Gateway started`, {
      port: config.server.port,
      env: config.env,
      nodeVersion: process.version,
    });
  });

  // ── Graceful shutdown ────────────────────────────────────────────────────────
  //
  // On SIGTERM/SIGINT:
  //   1. Stop accepting new connections (server.close).
  //   2. Wait for in-flight requests to finish (up to 10 s).
  //   3. Close MongoDB connection.
  //   4. Exit cleanly.
  //
  // Kubernetes sends SIGTERM before killing the pod; this gives the gateway
  // time to drain SSE connections rather than cutting them mid-stream.

  const SHUTDOWN_TIMEOUT_MS = 10_000;

  async function shutdown(signal) {
    logger.info(`Received ${signal} — starting graceful shutdown`);

    server.close(async () => {
      logger.info('HTTP server closed — no new connections accepted');
      await disconnectDB();
      logger.info('Shutdown complete');
      process.exit(0);
    });

    // Force-kill if drain takes too long.
    setTimeout(() => {
      logger.error('Graceful shutdown timed out — forcing exit');
      process.exit(1);
    }, SHUTDOWN_TIMEOUT_MS).unref(); // .unref() so it doesn't keep the event loop alive
  }

  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));

  // Catch unhandled promise rejections — log and exit so the orchestrator
  // can restart the pod rather than running a zombie process.
  process.on('unhandledRejection', (reason) => {
    logger.error('Unhandled promise rejection', { reason: String(reason) });
    process.exit(1);
  });

  process.on('uncaughtException', (err) => {
    logger.error('Uncaught exception', { message: err.message, stack: err.stack });
    process.exit(1);
  });

  return server;
}

bootstrap().catch((err) => {
  // Logger may not be initialised yet if config failed — use console as fallback.
  // eslint-disable-next-line no-console
  console.error('Failed to start server:', err);
  process.exit(1);
});


// cors = require('cors');
// app.use(cors({
//   origin: 'http://localhost:3001',
//   credentials: true,
// }));

module.exports = { createApp }; // exported for test suites
