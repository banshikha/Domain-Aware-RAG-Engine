'use strict';

const router = require('express').Router();
// 👈 FIXED: Destructure the specific middleware you exported
const { uploadSingle } = require('../middleware/upload'); 
const documentController = require('../controllers/documentController');

// POST /api/v1/ingest
router.post(
  '/',
  uploadSingle, // 👈 FIXED: Just pass the array directly
  documentController.uploadDocument
);

module.exports = router;