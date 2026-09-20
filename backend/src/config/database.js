'use strict';

const mongoose = require('mongoose');
const config = require('./index');
const logger = require('./logger');

const MONGO_OPTIONS = {
  maxPoolSize: config.mongo.poolSize,
  serverSelectionTimeoutMS: 5000,
  socketTimeoutMS: 45000,
  // Disable mongoose buffering so operations fail-fast if DB is unreachable.
  bufferCommands: false,
};

let isConnected = false;

/**
 * Attempts MongoDB connection with exponential back-off.
 * Retries indefinitely in production so a transient DB restart
 * does not permanently crash the gateway process.
 */
async function connectDB(attempt = 1) {
  const maxDelay = 30000; // cap back-off at 30 s
  const delay = Math.min(1000 * 2 ** (attempt - 1), maxDelay);

  try {
    logger.info(`MongoDB: connecting (attempt ${attempt})…`, {
      uri: config.mongo.uri.replace(/\/\/.*@/, '//***@'), // redact credentials
    });

    await mongoose.connect(config.mongo.uri, MONGO_OPTIONS);
    isConnected = true;
    logger.info('MongoDB: connection established');
  } catch (err) {
    logger.error(`MongoDB: connection failed — retrying in ${delay}ms`, {
      message: err.message,
      attempt,
    });
    await new Promise((resolve) => setTimeout(resolve, delay));
    return connectDB(attempt + 1);
  }
}

/**
 * Gracefully closes the Mongoose connection.
 * Called from process signal handlers in server.js.
 */
async function disconnectDB() {
  if (!isConnected) return;
  await mongoose.disconnect();
  isConnected = false;
  logger.info('MongoDB: connection closed');
}

// ── Mongoose event listeners ──────────────────────────────────────────────────

mongoose.connection.on('disconnected', () => {
  isConnected = false;
  logger.warn('MongoDB: disconnected');
});

mongoose.connection.on('reconnected', () => {
  isConnected = true;
  logger.info('MongoDB: reconnected');
});

mongoose.connection.on('error', (err) => {
  logger.error('MongoDB: runtime error', { message: err.message });
});

// Surface slow queries in development.
if (config.isDevelopment) {
  mongoose.set('debug', (collectionName, method, query, doc) => {
    logger.debug('MongoDB query', { collectionName, method, query });
  });
}

module.exports = { connectDB, disconnectDB, isConnected: () => isConnected };
