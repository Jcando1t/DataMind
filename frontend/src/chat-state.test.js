import assert from 'node:assert/strict';
import test from 'node:test';
import { CHAT_STORAGE_KEY, createConversation, deleteConversation, loadChatState, saveChatState, updateConversation } from './chat-state.js';

function memoryStorage() {
  const data = new Map();
  return { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
}

test('conversation history is saved and restored with the selected chat', () => {
  const storage = memoryStorage();
  const first = createConversation('first');
  const second = createConversation('second');
  const state = { conversations: [second, first], activeId: 'first' };
  assert.equal(saveChatState(state, storage), true);
  assert.deepEqual(loadChatState(storage), state);
  assert.ok(storage.getItem(CHAT_STORAGE_KEY));
});

test('the first question becomes the title and the updated conversation sorts first', () => {
  const old = { ...createConversation('old'), updatedAt: 1 };
  const active = { ...createConversation('active'), updatedAt: 2 };
  const messages = [{ role: 'user', text: '公司一季度销售表现如何？', time: '09:30' }];
  const state = updateConversation({ conversations: [old, active], activeId: 'active' }, 'active', messages, 10);
  assert.equal(state.conversations[0].id, 'active');
  assert.equal(state.conversations[0].title, messages[0].text);
  assert.deepEqual(state.conversations[0].messages, messages);
});

test('corrupt or unavailable storage starts with a usable blank chat', () => {
  const broken = { getItem: () => '{bad', setItem: () => { throw new Error('quota'); } };
  const state = loadChatState(broken);
  assert.equal(state.conversations.length, 1);
  assert.equal(state.conversations[0].title, '新对话');
  assert.equal(saveChatState(state, broken), false);
});

test('deletion preserves other chats and selects a usable chat after reload', () => {
  const storage = memoryStorage();
  const first = createConversation('first');
  const last = createConversation('last');
  const state = { conversations: [first, last], activeId: first.id };
  assert.deepEqual(deleteConversation(state, last.id), { conversations: [first], activeId: first.id });
  const afterDelete = deleteConversation(state, first.id);
  assert.equal(saveChatState(afterDelete, storage), true);
  assert.deepEqual(loadChatState(storage), { conversations: [last], activeId: last.id });
  const blank = deleteConversation(afterDelete, last.id);
  assert.equal(blank.conversations.length, 1);
  assert.equal(blank.activeId, blank.conversations[0].id);
  assert.notEqual(blank.activeId, last.id);
  assert.deepEqual(blank.conversations[0].messages, []);
});
