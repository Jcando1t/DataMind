export const CHAT_STORAGE_KEY = 'datamind.chat-state.v1';

export function createConversation(id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`) {
  return { id, title: '新对话', messages: [], updatedAt: Date.now() };
}

export function loadChatState(storage) {
  try {
    storage ||= globalThis.localStorage;
    const saved = JSON.parse(storage.getItem(CHAT_STORAGE_KEY) || 'null');
    const conversations = Array.isArray(saved?.conversations)
      ? saved.conversations.filter((chat) => chat && typeof chat.id === 'string' && Array.isArray(chat.messages))
      : [];
    if (conversations.length) {
      const activeId = conversations.some((chat) => chat.id === saved.activeId) ? saved.activeId : conversations[0].id;
      return { conversations, activeId };
    }
  } catch { /* 首次打开或本地存储不可用时，创建空白会话。 */ }
  const conversation = createConversation();
  return { conversations: [conversation], activeId: conversation.id };
}

export function saveChatState(state, storage) {
  try {
    storage ||= globalThis.localStorage;
    storage.setItem(CHAT_STORAGE_KEY, JSON.stringify(state));
    return true;
  } catch {
    return false;
  }
}

export function deleteConversation(state, id) {
  const conversations = state.conversations.filter((chat) => chat.id !== id);
  if (conversations.length === state.conversations.length) return state;
  if (!conversations.length) conversations.push(createConversation());
  return { conversations, activeId: state.activeId === id ? conversations[0].id : state.activeId };
}

export function updateConversation(state, id, messages, updatedAt = Date.now()) {
  const firstQuestion = messages.find((message) => message.role === 'user')?.text?.trim();
  return {
    ...state,
    conversations: state.conversations.map((chat) => chat.id !== id ? chat : {
      ...chat,
      messages,
      title: chat.title === '新对话' && firstQuestion ? Array.from(firstQuestion).slice(0, 32).join('') : chat.title,
      updatedAt,
    }).sort((a, b) => b.updatedAt - a.updatedAt),
  };
}
