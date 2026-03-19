/**
 * Hashtee AI Agent
 * Powers the website chatbot via NanoClaw's web channel /query endpoint.
 * Knowledge is loaded from hashtee-knowledge.txt — edit that file and restart NanoClaw to update.
 */

import fs from 'fs';
import path from 'path';

const PROXY_URL = 'http://localhost:4000';

export type Message = { role: 'user' | 'assistant'; content: string };

// ── Load knowledge from file ──────────────────────────────────────────────────
function loadKnowledge(): string {
  const knowledgePath = path.resolve(process.cwd(), 'hashtee-knowledge.txt');
  try {
    const content = fs.readFileSync(knowledgePath, 'utf-8').trim();
    if (!content) throw new Error('Knowledge file is empty');
    console.log(`[Hashtee Agent] Knowledge loaded from ${knowledgePath}`);
    return content;
  } catch (e) {
    console.warn(`[Hashtee Agent] WARNING: Could not load knowledge — ${e}`);
    return 'Hashtee builds custom AI systems for industry. Website: https://hashteelab.com';
  }
}

const KNOWLEDGE = loadKnowledge();

// ── Emotional detection ───────────────────────────────────────────────────────
const EMOTIONAL_KEYWORDS = [
  'not feeling well', 'feeling sick', 'im sick', "i'm sick", 'feel sick',
  'not well', 'not okay', 'not ok', "i'm not ok", 'im not ok',
  'tired', 'exhausted', 'burnout', 'burned out',
  'stressed', 'stress', 'overwhelmed',
  'sad', 'unhappy', 'depressed', 'lonely',
  'anxious', 'anxiety', 'worried', 'scared', 'nervous',
  'angry', 'frustrated', 'upset', 'hurt',
  'crying', 'terrible', 'awful', 'horrible',
  'headache', 'fever', 'unwell', 'ill',
  'happy', 'excited', 'feeling great', 'feeling good',
];

const GENERAL_KEYWORDS = [
  'hello', 'hi ', ' hi', 'hey', 'good morning', 'good evening',
  'good afternoon', 'good night', 'how are you', "what's up", 'wassup',
  'sup', 'bye', 'goodbye', 'thanks', 'thank you', 'ok', 'okay', 'cool',
  'nice', 'great', 'haha', 'lol', 'who are you', 'what are you',
  'my name', 'i am ', "i'm ", 'call me', 'nice to meet', 'pleasure',
  'how old', 'where are you', 'what do you do', 'can you help',
  'tell me about yourself', 'what can you do',
];

function isEmotional(message: string): boolean {
  const lower = message.toLowerCase();
  return EMOTIONAL_KEYWORDS.some((k) => lower.includes(k));
}

function isGeneralQuery(message: string): boolean {
  const lower = message.toLowerCase().trim();
  return GENERAL_KEYWORDS.some((k) => lower.includes(k));
}

// ── Relevant chunk finder ─────────────────────────────────────────────────────
function findRelevantChunk(query: string): string {
  const lower = query.toLowerCase();
  // Split knowledge into sections by ## or ###
  const sections = KNOWLEDGE.split(/\n(?=##)/);
  // Score each section by keyword overlap
  const scored = sections.map(section => {
    const sectionLower = section.toLowerCase();
    const words = lower.replace(/[^a-z0-9\s]/g, ' ').split(/\s+/).filter(w => w.length > 2);
    const score = words.filter(w => sectionLower.includes(w)).length;
    return { section, score };
  });
  scored.sort((a, b) => b.score - a.score);
  // Return top 2 sections
  return scored.slice(0, 2).map(s => s.section.trim()).join('\n\n');
}

// ── Prompts ───────────────────────────────────────────────────────────────────
const SYSTEM_PROMPT = `You are the official AI assistant for Hashtee — a company that builds custom AI systems for large enterprises. You are part of the Hashtee team.

You handle all types of messages:
- Greetings, small talk, personal messages: respond warmly and naturally like a friendly human. Keep it short.
- Emotional messages (stress, illness, feelings): respond with genuine empathy like a caring friend. Do NOT mention Hashtee.
- Questions about Hashtee: answer using ONLY the knowledge provided in the message. Use "we" and "our". Be specific and accurate.

Never say you don't have information if it is provided to you in the message. Keep answers concise and conversational. No bullet points or markdown.`;

// ── LLM call via proxy ────────────────────────────────────────────────────────
async function callProxy(messages: Message[], system: string): Promise<string> {
  const body = JSON.stringify({
    model: 'claude-3-haiku-20240307',
    max_tokens: 600,
    temperature: 0.7,
    system,
    messages,
  });

  const res = await fetch(PROXY_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
  });

  const data = (await res.json()) as Record<string, unknown>;
  const content = data?.content as Array<{ type: string; text: string }> | undefined;
  const text = content?.[0]?.text?.trim() ?? '';
  if (!text) throw new Error(`Empty LLM response: ${JSON.stringify(data)}`);
  return text;
}

// ── Main agent ────────────────────────────────────────────────────────────────
export async function runHashteeAgent(
  userMessage: string,
  history: Message[],
): Promise<{ reply: string; updatedHistory: Message[] }> {
  // Always use a single system prompt — avoids confusion from switching prompts mid-conversation
  const relevantChunk = findRelevantChunk(userMessage);
  const userContent = relevantChunk
    ? `[Hashtee Knowledge]\n${relevantChunk}\n\n[User Message]\n${userMessage}`
    : userMessage;

  const messages: Message[] = [
    ...history,
    { role: 'user', content: userContent },
  ];

  const reply = await callProxy(messages, SYSTEM_PROMPT);
  messages.push({ role: 'assistant', content: reply });
  return { reply, updatedHistory: messages };
}
