'use strict';

const fs = require('fs');
const path = require('path');
const Document = require('../models/Document');
const mlService = require('../services/mlService');
const { HTTP, INGESTION_STATUS, ROLES } = require('../config/constants');

const DEV_USER_ID = '64b000000000000000000000';

// ── Upload document ────────────────────────────────────────────────────────────

async function uploadDocument(req, res, next) {
  if (!req.file) {
    return res.status(HTTP.BAD_REQUEST).json({
      success: false,
      error: 'No file received. Ensure the field name is "file" and Content-Type is multipart/form-data.',
    });
  }

  const domain = req.domain || req.body?.domain || 'general';
  const userId = req.user?.id || DEV_USER_ID;

  let document;

  try {
    // ── 1. Persist document record ────────────────────────────────────────────
    document = await Document.create({
      userId,
      domain,
      originalName: req.file.originalname,
      storedName: req.file.filename,
      mimeType: req.file.mimetype,
      sizeBytes: req.file.size,
      storagePath: req.file.path,
      status: INGESTION_STATUS.PENDING,
    });

    req.log.info('Document record created', {
      documentId: document._id,
      originalName: req.file.originalname,
      domain,
      sizeBytes: req.file.size,
    });

    // ── 2. Dispatch to ML service (Mocked for local dev if down) ──────────────
    let jobId = `mock-job-${Date.now()}`;
    
    try {
      const ingestionResult = await mlService.triggerIngestion({
        documentId: document._id.toString(),
        storagePath: req.file.path,
        domain,
        mimeType: req.file.mimetype,
        requestId: req.requestId,
        userId,
      });
      jobId = ingestionResult.job_id;
    } catch (mlErr) {
      req.log.warn('ML Service unreachable, using mock ingestion job ID for local dev.');
      // We don't throw here so the file upload still succeeds in the UI
    }

    // ── 3. Update record with job ID ──────────────────────────────────────────
    document.status = INGESTION_STATUS.PROCESSING;
    document.jobId = jobId;
    await document.save();

    req.log.info('Ingestion job dispatched', {
      documentId: document._id,
      jobId,
    });

    return res.status(HTTP.CREATED).json({
      success: true,
      data: { document },
    });
  } catch (err) {
    if (document) {
      document.status = INGESTION_STATUS.FAILED;
      document.errorMessage = err.message;
      await document.save().catch(() => {}); 
    }

    if (req.file?.path) {
      fs.unlink(req.file.path, () => {});
    }

    next(err);
  }
}

// ── List documents ─────────────────────────────────────────────────────────────

async function listDocuments(req, res, next) {
  try {
    const { domain, status, limit = 20, skip = 0 } = req.query;
    const userId = req.user?.id || DEV_USER_ID;

    const filter = {
      userId,
      isDeleted: false,
      ...(domain ? { domain } : {}),
      ...(status ? { status } : {}),
    };

    const [documents, total] = await Promise.all([
      Document.find(filter)
        .select('originalName storedName mimeType sizeBytes domain status chunkCount jobId createdAt updatedAt')
        .sort({ createdAt: -1 })
        .skip(parseInt(skip, 10))
        .limit(Math.min(parseInt(limit, 10), 100))
        .lean(),
      Document.countDocuments(filter),
    ]);

    return res.status(HTTP.OK).json({
      success: true,
      data: { documents, total, limit: parseInt(limit, 10), skip: parseInt(skip, 10) },
    });
  } catch (err) {
    next(err);
  }
}

// ── Get single document ───────────────────────────────────────────────────────

async function getDocument(req, res, next) {
  try {
    const document = await Document.findOne({
      _id: req.params.documentId,
      userId: req.user?.id || DEV_USER_ID,
      isDeleted: false,
    }).lean();

    if (!document) {
      return res.status(HTTP.NOT_FOUND).json({ success: false, error: 'Document not found.' });
    }

    return res.status(HTTP.OK).json({ success: true, data: { document } });
  } catch (err) {
    next(err);
  }
}

// ── Poll ingestion status ──────────────────────────────────────────────────────

async function getIngestionStatus(req, res, next) {
  try {
    const document = await Document.findOne(
      { _id: req.params.documentId, userId: req.user?.id || DEV_USER_ID, isDeleted: false },
      { status: 1, chunkCount: 1, errorMessage: 1, updatedAt: 1 }
    ).lean();

    if (!document) {
      return res.status(HTTP.NOT_FOUND).json({ success: false, error: 'Document not found.' });
    }

    const payload = {
      status: document.status,
      chunkCount: document.chunkCount ?? null,
      updatedAt: document.updatedAt,
      ...(document.status === INGESTION_STATUS.FAILED
        ? { error: document.errorMessage || 'Ingestion failed.' }
        : {}),
    };

    return res.status(HTTP.OK).json({ success: true, data: payload });
  } catch (err) {
    next(err);
  }
}

// ── Webhook: ML service reports ingestion completion ──────────────────────────

async function ingestWebhook(req, res, next) {
  try {
    const { document_id, status, chunk_count, vector_ids, error_message } = req.body;

    const document = await Document.findById(document_id).select('+vectorIds +errorMessage');

    if (!document) {
      req.log.warn('Webhook received for unknown document', { documentId: document_id });
      return res.status(HTTP.OK).json({ success: true });
    }

    const allowedTransitions = {
      [INGESTION_STATUS.PENDING]: [INGESTION_STATUS.PROCESSING, INGESTION_STATUS.FAILED],
      [INGESTION_STATUS.PROCESSING]: [INGESTION_STATUS.COMPLETED, INGESTION_STATUS.FAILED],
    };

    const validNext = allowedTransitions[document.status] || [];
    if (!validNext.includes(status)) {
      req.log.warn('Invalid ingestion status transition', {
        documentId: document_id,
        from: document.status,
        to: status,
      });
      return res.status(HTTP.OK).json({ success: true }); 
    }

    document.status = status;
    if (chunk_count !== undefined) document.chunkCount = chunk_count;
    if (vector_ids) document.vectorIds = vector_ids;
    if (error_message) document.errorMessage = error_message;

    await document.save();

    req.log.info('Ingestion webhook processed', { documentId: document_id, status });

    return res.status(HTTP.OK).json({ success: true });
  } catch (err) {
    next(err);
  }
}

// ── Delete document ───────────────────────────────────────────────────────────

async function deleteDocument(req, res, next) {
  try {
    const document = await Document.findOne({
      _id: req.params.documentId,
      userId: req.user?.id || DEV_USER_ID,
      isDeleted: false,
    }).select('+storagePath +vectorIds');

    if (!document) {
      return res.status(HTTP.NOT_FOUND).json({ success: false, error: 'Document not found.' });
    }

    document.isDeleted = true;
    await document.save();

    if (document.storagePath) {
      fs.unlink(document.storagePath, (unlinkErr) => {
        if (unlinkErr) {
          req.log.warn('Failed to delete file from disk', {
            path: document.storagePath,
            message: unlinkErr.message,
          });
        }
      });
    }

    if (document.status === INGESTION_STATUS.COMPLETED) {
      mlService
        .deleteDocumentVectors({
          documentId: document._id.toString(),
          domain: document.domain,
          requestId: req.requestId,
        })
        .catch((vecErr) => {
          req.log.error('Failed to delete vectors from ML service', {
            documentId: document._id,
            message: vecErr.message,
          });
        });
    }

    req.log.info('Document deleted', { documentId: document._id });

    return res.status(HTTP.NO_CONTENT).send();
  } catch (err) {
    next(err);
  }
}

// ── Admin: list all documents ─────────────────────────────────────────────────

async function adminListDocuments(req, res, next) {
  try {
    const { userId, domain, status, limit = 50, skip = 0 } = req.query;

    const filter = {
      isDeleted: false,
      ...(userId ? { userId } : {}),
      ...(domain ? { domain } : {}),
      ...(status ? { status } : {}),
    };

    const [documents, total] = await Promise.all([
      Document.find(filter)
        .populate('userId', 'email name')
        .sort({ createdAt: -1 })
        .skip(parseInt(skip, 10))
        .limit(Math.min(parseInt(limit, 10), 200))
        .lean(),
      Document.countDocuments(filter),
    ]);

    return res.status(HTTP.OK).json({
      success: true,
      data: { documents, total },
    });
  } catch (err) {
    next(err);
  }
}

module.exports = {
  uploadDocument,
  listDocuments,
  getDocument,
  getIngestionStatus,
  ingestWebhook,
  deleteDocument,
  adminListDocuments,
};