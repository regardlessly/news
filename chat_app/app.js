/* ======================================================
   CaritaHub News Chat — Frontend
   ====================================================== */

const SESSION_KEY = 'cna_chat_session_id';

function getSessionId() {
  let id = sessionStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

let sessionId = getSessionId();
let isLoading = false;

// ---- DOM refs ----
const chatMessages = document.getElementById('chatMessages');
const chatInput    = document.getElementById('chatInput');
const sendBtn      = document.getElementById('sendBtn');
const newChatBtn   = document.getElementById('newChatBtn');
const themeToggle  = document.getElementById('themeToggle');

// ---- Theme ----
function initTheme() {
  const saved = localStorage.getItem('chat_theme') || 'light';
  if (saved === 'dark') {
    document.body.classList.add('dark');
    themeToggle.textContent = '☀️';
  }
}

function toggleTheme() {
  document.body.classList.toggle('dark');
  const isDark = document.body.classList.contains('dark');
  localStorage.setItem('chat_theme', isDark ? 'dark' : 'light');
  themeToggle.textContent = isDark ? '☀️' : '🌙';
}

// ---- Message rendering ----
function appendMessage(role, content) {
  const welcome = chatMessages.querySelector('.welcome-message');
  if (welcome) welcome.remove();

  const div = document.createElement('div');
  div.className = `message ${role}`;

  const avatarEmoji = role === 'user' ? '👤' : '🤖';

  div.innerHTML = `
    <div class="message-avatar">${avatarEmoji}</div>
    <div class="message-content">
      <div class="message-bubble">${escapeHtml(content)}</div>
    </div>
  `;
  chatMessages.appendChild(div);
  scrollToBottom();
  return div;
}

function showTypingIndicator() {
  const div = document.createElement('div');
  div.className = 'message assistant typing-indicator';
  div.id = 'typingIndicator';
  div.innerHTML = `
    <div class="message-avatar">🤖</div>
    <div class="message-content">
      <div class="message-bubble">
        <span class="dot"></span>
        <span class="dot"></span>
        <span class="dot"></span>
      </div>
    </div>
  `;
  chatMessages.appendChild(div);
  scrollToBottom();
}

function removeTypingIndicator() {
  const el = document.getElementById('typingIndicator');
  if (el) el.remove();
}

function scrollToBottom() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---- Send message ----
async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text || isLoading) return;

  isLoading = true;
  chatInput.value = '';
  chatInput.style.height = 'auto';
  sendBtn.disabled = true;

  appendMessage('user', text);
  showTypingIndicator();

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });

    removeTypingIndicator();

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      appendMessage('assistant', `Error: ${err.detail || resp.statusText}`);
    } else {
      const data = await resp.json();
      appendMessage('assistant', data.reply);
    }
  } catch (e) {
    removeTypingIndicator();
    appendMessage('assistant', 'Network error. Is the chat server running?');
  }

  isLoading = false;
  updateSendBtn();
}

// ---- New chat ----
function newChat() {
  sessionId = crypto.randomUUID();
  sessionStorage.setItem(SESSION_KEY, sessionId);

  chatMessages.innerHTML = `
    <div class="welcome-message">
      <div class="welcome-icon">🗞️</div>
      <h2>CaritaHub News Assistant</h2>
      <p>Ask me anything about recent news. I have access to articles from the past 7 days.</p>
    </div>
  `;
}

// ---- Input auto-resize ----
function autoResize() {
  chatInput.style.height = 'auto';
  chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + 'px';
}

function updateSendBtn() {
  sendBtn.disabled = !chatInput.value.trim() || isLoading;
}

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
  initTheme();

  themeToggle.addEventListener('click', toggleTheme);
  newChatBtn.addEventListener('click', newChat);
  sendBtn.addEventListener('click', sendMessage);

  chatInput.addEventListener('input', () => {
    autoResize();
    updateSendBtn();
  });

  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
});
