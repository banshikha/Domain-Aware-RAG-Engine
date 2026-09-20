'use strict';

const ChatSession = require('../models/ChatSession');
const mlService = require('../services/mlService');
const { HTTP, MESSAGE_ROLES } = require('../config/constants');

const DEV_USER_ID = '64b000000000000000000000';
const DEV_SESSION_ID = '64c000000000000000000000';

// ── Create session ─────────────────────────────────────────────────────────────

async function createSession(req, res, next) {
  try {
    const { domain, title, pinnedDocumentIds = [] } = req.body;

    const session = await ChatSession.create({
      userId: req.user?.id || DEV_USER_ID,
      domain,
      title: title || 'New conversation',
      pinnedDocumentIds,
    });

    req.log.info('Chat session created', { sessionId: session._id, domain });

    return res.status(HTTP.CREATED).json({
      success: true,
      data: { session },
    });
  } catch (err) {
    next(err);
  }
}

// ── List sessions ─────────────────────────────────────────────────────────────

async function listSessions(req, res, next) {
  try {
    const { domain, limit = 20, skip = 0, includeArchived = false } = req.query;

    const sessions = await ChatSession.findForUser(req.user?.id || DEV_USER_ID, {
      domain,
      limit: Math.min(parseInt(limit, 10), 100),
      skip: parseInt(skip, 10),
      includeArchived: includeArchived === 'true',
    });

    return res.status(HTTP.OK).json({
      success: true,
      data: { sessions, count: sessions.length },
    });
  } catch (err) {
    next(err);
  }
}

// ── Get single session ────────────────────────────────────────────────────────

async function getSession(req, res, next) {
  try {
    const session = await ChatSession.findOne({
      _id: req.params?.sessionId || DEV_SESSION_ID,
      userId: req.user?.id || DEV_USER_ID,
    });

    if (!session) {
      return res.status(HTTP.NOT_FOUND).json({
        success: false,
        error: 'Session not found.',
      });
    }

    return res.status(HTTP.OK).json({ success: true, data: { session } });
  } catch (err) {
    next(err);
  }
}

// ── Update session metadata ───────────────────────────────────────────────────

async function updateSession(req, res, next) {
  try {
    const { title, isArchived, pinnedDocumentIds } = req.body;

    const session = await ChatSession.findOne({
      _id: req.params?.sessionId || DEV_SESSION_ID,
      userId: req.user?.id || DEV_USER_ID,
    });

    if (!session) {
      return res.status(HTTP.NOT_FOUND).json({ success: false, error: 'Session not found.' });
    }

    if (title !== undefined) session.title = title;
    if (isArchived !== undefined) session.isArchived = isArchived;
    if (pinnedDocumentIds !== undefined) session.pinnedDocumentIds = pinnedDocumentIds;

    await session.save();

    req.log.info('Session updated', { sessionId: session._id });

    return res.status(HTTP.OK).json({ success: true, data: { session } });
  } catch (err) {
    next(err);
  }
}

// ── Delete session ────────────────────────────────────────────────────────────

async function deleteSession(req, res, next) {
  try {
    const sessionId = req.params?.sessionId || DEV_SESSION_ID;
    const result = await ChatSession.deleteOne({
      _id: sessionId,
      userId: req.user?.id || DEV_USER_ID,
    });

    if (result.deletedCount === 0) {
      return res.status(HTTP.NOT_FOUND).json({ success: false, error: 'Session not found.' });
    }

    req.log.info('Session deleted', { sessionId });

    return res.status(HTTP.NO_CONTENT).send();
  } catch (err) {
    next(err);
  }
}

// ── Stream query ──────────────────────────────────────────────────────────────

async function streamQuery(req, res, next) {
  const rawSessionId = req.params?.sessionId || null;
  const { query } = req.body;
  const domain = req.domain || req.body?.domain || 'general';
  const userId = req.user?.id || DEV_USER_ID;

  let session;
  let assistantContent = '';
  let citations = [];
  let tokenUsage = null;
  const startTime = Date.now();

  try {
    // ── 1. Load and authorise session ─────────────────────────────────────────
    
    // Only query DB if we have a valid 24-character hex string
    if (rawSessionId && rawSessionId.length === 24) {
      session = await ChatSession.findOne({ _id: rawSessionId, userId });
    }

    // If no session exists in DB or the ID was missing/invalid, use a mock object
    if (!session) {
      session = {
        _id: rawSessionId || DEV_SESSION_ID,
        domain,
        pinnedDocumentIds: [],
        appendMessage: async () => {}, // Mock persistence
        getRecentMessages: () => [],   // Mock history
      };
    }

    if (session.domain !== domain) {
      return res.status(HTTP.BAD_REQUEST).json({
        success: false,
        error: `Domain mismatch: session domain is '${session.domain}', request domain is '${domain}'.`,
      });
    }

    // ── 2. Persist the user message immediately ───────────────────────────────
    await session.appendMessage({ role: MESSAGE_ROLES.USER, content: query });

    // ── 3. Build context window ───────────────────────────────────────────────
    const recentMessages = session.getRecentMessages(11).slice(0, -1);
    const history = recentMessages.map((m) => ({ role: m.role, content: m.content }));

    req.log.info('Streaming query to ML service', {
      sessionId: session._id,
      domain,
      historyLength: history.length,
      queryLength: query.length,
    });

    // ── 4 & 5. Open SSE and proxy the stream ─────────────────────────────────
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');
    res.setHeader('X-Accel-Buffering', 'no');

    const axios = require('axios');
    const config = require('../config');
    const { HEADERS } = require('../config/constants');

    let mlResponse;

    try {
      // Try to hit the actual ML service
      mlResponse = await axios.default({
        method: 'POST',
        url: `${config.mlService.url}/api/v1/query`,
        data: { query, history, pinned_document_ids: session.pinnedDocumentIds },
        responseType: 'stream',
        timeout: config.mlService.timeout,
        headers: {
          'Content-Type': 'application/json',
          [HEADERS.ML_API_KEY]: config.mlService.apiKey,
          [HEADERS.DOMAIN]: domain,
          [HEADERS.REQUEST_ID]: req.requestId,
          [HEADERS.USER_ID]: userId,
        },
      });
    } catch (mlError) {
      // ML SERVICE IS DOWN / NOT RUNNING -> Send the Mock Response
      res.write(`data: ${JSON.stringify({
        type: 'token',
        content: 'Backend connected. ',
      })}\n\n`);

      setTimeout(() => {
        res.write(`data: ${JSON.stringify({
          type: 'token',
          content: 'ML service is currently offline. This is a mocked stream response.',
        })}\n\n`);
      }, 300);

      setTimeout(() => {
        res.write(`data: ${JSON.stringify({
          type: 'done',
          citations: [],
        })}\n\n`);
        res.end();
      }, 600);

      return; // Stop execution here so we don't try to parse mlResponse
    }

    let buffer = '';

    mlResponse.data.on('data', (chunk) => {
      const text = chunk.toString();
      buffer += text;

      if (!res.writableEnded) res.write(chunk);

      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') continue;

        try {
          const event = JSON.parse(payload);
          if (event.type === 'token' && event.content) {
            assistantContent += event.content;
          } else if (event.type === 'done') {
            citations = event.citations || [];
            tokenUsage = event.token_usage || null;
          }
        } catch {
          // Non-JSON SSE line — skip silently.
        }
      }
    });

    mlResponse.data.on('end', async () => {
      if (!res.writableEnded) res.end();

      if (assistantContent) {
        try {
          await session.appendMessage({
            role: MESSAGE_ROLES.ASSISTANT,
            content: assistantContent,
            citations,
            tokenUsage: tokenUsage
              ? {
                  promptTokens: tokenUsage.prompt_tokens,
                  completionTokens: tokenUsage.completion_tokens,
                  totalTokens: tokenUsage.total_tokens,
                }
              : undefined,
            latencyMs: Date.now() - startTime,
          });
        } catch (saveErr) {
          req.log.error('Failed to persist assistant message', { message: saveErr.message });
        }
      }
    });

    mlResponse.data.on('error', (streamErr) => {
      req.log.error('ML stream error', { message: streamErr.message });
      if (!res.writableEnded) {
        res.write(`data: ${JSON.stringify({ type: 'error', message: 'Stream interrupted.' })}\n\n`);
        res.end();
      }
    });

    res.on('close', () => {
      if (mlResponse && mlResponse.data && !mlResponse.data.destroyed) {
        mlResponse.data.destroy();
      }
    });
  } catch (err) {
    if (!res.headersSent) return next(err);
    req.log.error('Error during stream', { message: err.message });
    if (!res.writableEnded) {
      res.write(`data: ${JSON.stringify({ type: 'error', message: 'Internal error.' })}\n\n`);
      res.end();
    }
  }
}

// ── Get message history ───────────────────────────────────────────────────────

async function getMessages(req, res, next) {
  try {
    const sessionId = req.params?.sessionId || DEV_SESSION_ID;
    const { limit = 50, before } = req.query;

    const session = await ChatSession.findOne(
      { _id: sessionId, userId: req.user?.id || DEV_USER_ID },
      { messages: 1, domain: 1, title: 1 }
    );

    if (!session) {
      return res.status(HTTP.NOT_FOUND).json({ success: false, error: 'Session not found.' });
    }

    let messages = session.messages;

    if (before) {
      const idx = messages.findIndex((m) => m._id.toString() === before);
      if (idx > 0) messages = messages.slice(0, idx);
    }

    const pageSize = Math.min(parseInt(limit, 10), 100);
    messages = messages.slice(-pageSize);

    return res.status(HTTP.OK).json({
      success: true,
      data: { messages, hasMore: session.messages.length > pageSize },
    });
  } catch (err) {
    next(err);
  }
}

module.exports = {
  createSession,
  listSessions,
  getSession,
  updateSession,
  deleteSession,
  streamQuery,
  getMessages,
};