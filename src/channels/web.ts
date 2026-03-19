import fs from 'fs';
import http from 'http';
import path from 'path';
import { GROUPS_DIR } from '../config.js';
import { logger } from '../logger.js';
import { registerChannel, ChannelOpts } from './registry.js';
import {
  Channel,
  OnChatMetadata,
  OnInboundMessage,
  RegisteredGroup,
} from '../types.js';
export interface WebChannelOpts {
  onMessage: OnInboundMessage;
  onChatMetadata: OnChatMetadata;
  registeredGroups: () => Record<string, RegisteredGroup>;
  onRegisterGroup: (jid: string, group: RegisteredGroup) => void;
}

export class WebChannel implements Channel {
  name = 'web';
  private server: http.Server | null = null;
  private opts: WebChannelOpts;
  private port: number;
  private outboundBuffer: Map<string, string[]> = new Map();
  private chatSessions: Map<string, string[]> = new Map();

  constructor(port: number, opts: WebChannelOpts) {
    this.port = port;
    this.opts = opts;
  }

  async connect(): Promise<void> {
    this.server = http.createServer((req, res) => {
      // Basic CORS
      res.setHeader('Access-Control-Allow-Origin', '*');
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

      if (req.method === 'OPTIONS') {
        res.writeHead(204);
        res.end();
        return;
      }

      const url = new URL(req.url || '', `http://${req.headers.host}`);

      if (req.method === 'POST' && url.pathname === '/message') {
        let body = '';
        req.on('data', (chunk) => {
          body += chunk;
        });
        req.on('end', () => {
          try {
            const { chatJid, content, senderName } = JSON.parse(body);
            if (!chatJid || !content) {
              res.writeHead(400);
              res.end(JSON.stringify({ error: 'Missing chatJid or content' }));
              return;
            }

            const timestamp = new Date().toISOString();
            const sender = 'web-user';
            const msgId = `web-${Date.now()}`;

            // Register chat metadata if not known
            this.opts.onChatMetadata(chatJid, timestamp, chatJid, 'web', false);

            // Check if group is registered
            const group = this.opts.registeredGroups()[chatJid];
            if (!group) {
              res.writeHead(403);
              res.end(
                JSON.stringify({ error: 'Chat not registered in NanoClaw' }),
              );
              return;
            }

            // Deliver message to NanoClaw
            this.opts.onMessage(chatJid, {
              id: msgId,
              chat_jid: chatJid,
              sender,
              sender_name: senderName || 'User',
              content,
              timestamp,
              is_from_me: false,
            });

            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ ok: true, msgId }));
          } catch (e) {
            res.writeHead(400);
            res.end(JSON.stringify({ error: 'Invalid JSON' }));
          }
        });
        return;
      }

      if (req.method === 'POST' && url.pathname === '/create-session') {
        let body = '';
        req.on('data', (chunk) => {
          body += chunk;
        });
        req.on('end', () => {
          try {
            const { userId, userName = 'User' } = JSON.parse(body) as {
              userId?: string;
              userName?: string;
            };
            if (!userId) {
              res.writeHead(400, { 'Content-Type': 'application/json' });
              res.end(JSON.stringify({ error: 'userId is required' }));
              return;
            }
            const chatJid = `web:${userId}`;
            const folder = `web-${userId}`;

            // Return existing session if already registered
            const existing = this.opts.registeredGroups()[chatJid];
            if (existing) {
              res.writeHead(200, { 'Content-Type': 'application/json' });
              res.end(
                JSON.stringify({ ok: true, chatJid, already_existed: true }),
              );
              return;
            }

            // Register group in NanoClaw
            this.opts.onRegisterGroup(chatJid, {
              name: userName,
              folder,
              trigger: '',
              added_at: new Date().toISOString(),
              requiresTrigger: false,
            });

            // Write CLAUDE.md for this user's bot
            const groupDir = path.join(GROUPS_DIR, folder);
            fs.mkdirSync(groupDir, { recursive: true });
            const claudeMd = path.join(groupDir, 'CLAUDE.md');
            if (!fs.existsSync(claudeMd)) {
              fs.writeFileSync(
                claudeMd,
                `# Personal Assistant for ${userName}\n\n` +
                  `You are a helpful personal AI assistant for ${userName}, embedded in the Hashtee website.\n\n` +
                  `## What you can do\n` +
                  `- Answer questions about Hashtee and its products\n` +
                  `- Answer general knowledge questions\n` +
                  `- Help draft text, emails, or messages\n` +
                  `- Remember preferences across conversations\n\n` +
                  `## Rules\n` +
                  `- NEVER pretend to open pages or perform actions you cannot actually do\n` +
                  `- If asked to navigate somewhere, give the direct link instead (e.g. hashteelab.com/about)\n` +
                  `- Be concise, honest, and friendly\n` +
                  `- Do not make up information\n`,
              );
            }

            logger.info({ chatJid, userId, userName }, 'Web session created');
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ ok: true, chatJid }));
          } catch (e) {
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'Invalid JSON' }));
          }
        });
        return;
      }

      if (req.method === 'POST' && url.pathname === '/query') {
        let body = '';
        req.on('data', (chunk) => {
          body += chunk;
        });
        req.on('end', async () => {
          try {
            const { prompt, sessionId = 'default' } = JSON.parse(body) as {
              prompt?: string;
              sessionId?: string;
            };
            if (!prompt) {
              res.writeHead(400, { 'Content-Type': 'application/json' });
              res.end(JSON.stringify({ error: 'prompt is required' }));
              return;
            }
            logger.info(
              { sessionId, prompt: prompt.slice(0, 80) },
              'Hashtee agent query',
            );
            // Forward to tastebud which has full embeddings + ChromaDB RAG
            const tastebudUrl =
              process.env.TASTEBUD_URL || 'http://localhost:8000';
            const tastebudRes = await fetch(`${tastebudUrl}/query`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ prompt, sessionId }),
            });
            const tastebudData = (await tastebudRes.json()) as {
              reply: string;
            };
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ reply: tastebudData.reply, sessionId }));
          } catch (e) {
            logger.error({ err: e }, 'Hashtee agent error');
            res.writeHead(500, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: String(e) }));
          }
        });
        return;
      }

      if (req.method === 'GET' && url.pathname.startsWith('/poll/')) {
        const chatJid = url.pathname.replace('/poll/', '');
        const messages = this.outboundBuffer.get(chatJid) || [];
        this.outboundBuffer.set(chatJid, []); // Clear buffer after polling

        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ messages }));
        return;
      }

      res.writeHead(404);
      res.end();
    });

    return new Promise<void>((resolve) => {
      this.server!.listen(this.port, () => {
        logger.info({ port: this.port }, 'Web channel API listening');
        console.log(
          `\n  Web channel API: http://localhost:${this.port}/message`,
        );
        resolve();
      });
    });
  }

  async sendMessage(jid: string, text: string): Promise<void> {
    const buffer = this.outboundBuffer.get(jid) || [];
    buffer.push(text);
    this.outboundBuffer.set(jid, buffer);
    logger.info({ jid, length: text.length }, 'Web message buffered');
  }

  isConnected(): boolean {
    return this.server !== null;
  }

  ownsJid(jid: string): boolean {
    return jid.startsWith('web:');
  }

  async disconnect(): Promise<void> {
    if (this.server) {
      this.server.close();
      this.server = null;
      logger.info('Web channel API stopped');
    }
  }
}

registerChannel('web', (opts: ChannelOpts) => {
  const port = parseInt(process.env.WEB_CHANNEL_PORT || '3001');
  return new WebChannel(port, opts);
});
