import http from 'http';
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
}

export class WebChannel implements Channel {
    name = 'web';
    private server: http.Server | null = null;
    private opts: WebChannelOpts;
    private port: number;
    private outboundBuffer: Map<string, string[]> = new Map();

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
                req.on('data', chunk => { body += chunk; });
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
                            res.end(JSON.stringify({ error: 'Chat not registered in NanoClaw' }));
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
                console.log(`\n  Web channel API: http://localhost:${this.port}/message`);
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
