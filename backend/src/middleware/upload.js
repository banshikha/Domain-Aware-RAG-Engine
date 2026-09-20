'use strict';

const multer = require('multer');
const path = require('path');
const fs = require('fs');
const { v4: uuidv4 } = require('uuid');
const config = require('../config');
const { HTTP } = require('../config/constants');

// Fallback user ID for local testing
const DEV_USER_ID = '64b000000000000000000000';

// Ensure the upload directory exists at startup.
const uploadDir = path.resolve(config.upload.dir);
if (!fs.existsSync(uploadDir)) {
  fs.mkdirSync(uploadDir, { recursive: true });
}

/**
 * Disk storage engine.
 */
const storage = multer.diskStorage({
  destination(req, _file, cb) {
    // 👈 FIXED: Use fallback if req.user is undefined
    const userId = req.user?.id || DEV_USER_ID; 
    const userDir = path.join(uploadDir, userId.toString());
    
    if (!fs.existsSync(userDir)) {
      fs.mkdirSync(userDir, { recursive: true });
    }
    cb(null, userDir);
  },

  filename(_req, file, cb) {
    const ext = path.extname(file.originalname).toLowerCase();
    cb(null, `${uuidv4()}${ext}`);
  },
});

/**
 * MIME-type whitelist filter.
 */
function fileFilter(_req, file, cb) {
  if (!config.upload.allowedMimeTypes.includes(file.mimetype)) {
    return cb(
      Object.assign(new Error(`Unsupported file type: ${file.mimetype}`), { code: 'INVALID_MIME' }),
      false
    );
  }
  cb(null, true);
}

const upload = multer({
  storage,
  fileFilter,
  limits: {
    fileSize: config.upload.maxFileSizeBytes,
    files: 5,
  },
});

/**
 * Wraps multer errors.
 */
function handleMulterError(err, req, res, next) {
  if (err instanceof multer.MulterError) {
    const messages = {
      LIMIT_FILE_SIZE: `File too large. Maximum size is ${config.upload.maxFileSizeBytes / 1024 / 1024} MB.`,
      LIMIT_FILE_COUNT: 'Too many files. Maximum 5 files per request.',
      LIMIT_UNEXPECTED_FILE: 'Unexpected field name in upload.',
    };
    return res.status(HTTP.BAD_REQUEST).json({
      success: false,
      error: messages[err.code] || `Upload error: ${err.message}`,
      code: err.code,
    });
  }

  if (err?.code === 'INVALID_MIME') {
    return res.status(HTTP.BAD_REQUEST).json({
      success: false,
      error: err.message,
      allowedTypes: config.upload.allowedMimeTypes,
    });
  }

  next(err);
}

const uploadSingle = [upload.single('file'), handleMulterError];
const uploadMultiple = [upload.array('files', 5), handleMulterError];

module.exports = { uploadSingle, uploadMultiple, handleMulterError };