/* ======================================================
   CNA News Chat — Frontend
   ====================================================== */

const SESSION_KEY = 'cna_chat_session_id';

// Generate or restore session ID
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
const chatMessages  = document.getElementById('chatMessages');
const chatInput     = document.getElementById('chatInput');
const sendBtn       = document.getElementById('sendBtn');
const newChatBtn    = document.getElementById('newChatBtn');
const themeToggle   = document.getElementById('themeToggle');
const sidebarInfo   = document.getElementById('sidebarInfo');

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

// ---- Status ----
async function fetchStatus() {
  try {
    const resp = await fetch('/api/status');
    const data = await resp.json();
    const count = data.articles_week || 0;
    const today = data.articles_today || 0;
    const last = data.last_scraped ? relativeTime(data.last_scraped) : 'never';
    sidebarInfo.innerHTML = `
      <strong>${today}</strong> articles today<br>
      <strong>${count}</strong> articles this week<br>
      Last fetched: ${last}
    `;
  } catch {
    sidebarInfo.innerHTML = `<span>Could not load status</span>`;
  }
}

function relativeTime(dateStr) {
  if (!dateStr) return 'unknown';
  const date = new Date(dateStr);
  if (isNaN(date)) return 'unknown';
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ---- Message rendering ----
function appendMessage(role, content, sources = []) {
  // Remove welcome message if present
  const welcome = chatMessages.querySelector('.welcome-message');
  if (welcome) welcome.remove();

  const div = document.createElement('div');
  div.className = `message ${role}`;

  const avatarEmoji = role === 'user' ? '👤' : '🤖';
  const sourcesHtml = sources.length
    ? `<div class="message-sources">
         ${sources.map(s =>
           `<a class="source-chip" href="${s.url}" target="_blank" rel="noopener" title="${s.title}">${s.title}</a>`
         ).join('')}
       </div>`
    : '';

  div.innerHTML = `
    <div class="message-avatar">${avatarEmoji}</div>
    <div class="message-content">
      <div class="message-bubble">${escapeHtml(content)}</div>
      ${sourcesHtml}
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
      appendMessage('assistant', data.reply, data.sources || []);
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
      <h2>CNA News Assistant</h2>
      <p>Ask me anything about recent news from Channel NewsAsia. I have access to articles from the past 7 days.</p>
      <p class="welcome-hint">Run <code>python fetch_news.py</code> to fetch the latest news first.</p>
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

// ---- Hint clicks ----
function bindHintClicks() {
  document.querySelectorAll('.sidebar-hints li').forEach(li => {
    li.addEventListener('click', () => {
      chatInput.value = li.textContent.trim().replace(/^"|"$/g, '');
      chatInput.dispatchEvent(new Event('input'));
      chatInput.focus();
    });
  });
}

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  fetchStatus();
  bindHintClicks();

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
