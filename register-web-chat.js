import Database from 'better-sqlite3';
import path from 'path';

const dbPath = path.resolve('store', 'messages.db');
const db = new Database(dbPath);

const jid = 'web:test-chat';
const group = {
    name: 'Web Test Chat',
    folder: 'main', // Using the main folder
    trigger: '@Andy',
    added_at: new Date().toISOString(),
    requires_trigger: 0, // No trigger required for web chat
    is_main: 0
};

try {
    db.prepare(`
    INSERT OR REPLACE INTO registered_groups (jid, name, folder, trigger_pattern, added_at, requires_trigger, is_main)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `).run(
        jid,
        group.name,
        group.folder,
        group.trigger,
        group.added_at,
        group.requires_trigger,
        group.is_main
    );
    console.log(`Successfully registered ${jid}`);
} catch (err) {
    console.error('Failed to register group:', err);
} finally {
    db.close();
}
