/* ======================================================
   CNA News Digest — Frontend
   ====================================================== */

const LIMIT = 60;

const state = {
  section: 'all',
  days: 1,
  offset: 0,
  articles: [],
  hasMore: false,
};

// ---- Utility ----

function relativeTime(dateStr) {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  if (isNaN(date)) return '';
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function sectionTagClass(section) {
  const map = {
    singapore:    'tag-singapore',
    asia:         'tag-asia',
    world:        'tag-world',
    business:     'tag-business',
    sport:        'tag-sport',
    'top stories':'tag-top-stories',
  };
  return map[section?.toLowerCase()] || 'tag-top-stories';
}

function sectionDisplayName(section) {
  const map = {
    singapore:    'Singapore',
    asia:         'Asia',
    world:        'World',
    business:     'Business',
    sport:        'Sport',
    'top stories':'Top Stories',
  };
  return map[section?.toLowerCase()] || section || 'News';
}

// ---- API ----

async function apiFetch(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

// ---- Render ----

function renderCard(article) {
  const card = document.createElement('article');
  card.className = 'card';

  const title = article.title || 'Untitled';
  const summary = article.summary || 'Summary not available.';
  const timeStr = relativeTime(article.published_at || article.scraped_at);
  const section = article.section || '';
  const tagClass = sectionTagClass(section);
  const tagLabel = sectionDisplayName(section);

  card.innerHTML = `
    <div class="card-tags">
      <span class="tag ${tagClass}">${tagLabel}</span>
    </div>
    <a class="card-title" href="${article.url}" target="_blank" rel="noopener">
      ${title}
    </a>
    <p class="card-summary">${summary}</p>
    <div class="card-footer">
      <span class="card-time">${timeStr}</span>
      <a class="card-link" href="${article.url}" target="_blank" rel="noopener">Read →</a>
    </div>
  `;
  return card;
}

function groupBySection(articles) {
  const order = ['singapore', 'asia', 'world', 'business', 'sport'];
  const groups = {};
  for (const art of articles) {
    const s = art.section || 'other';
    if (!groups[s]) groups[s] = [];
    groups[s].push(art);
  }
  // Sort groups by preferred order
  const sorted = {};
  for (const s of order) {
    if (groups[s]) sorted[s] = groups[s];
  }
  for (const s of Object.keys(groups)) {
    if (!sorted[s]) sorted[s] = groups[s];
  }
  return sorted;
}

function renderGrid(articles, append = false) {
  const grid = document.getElementById('newsGrid');

  if (!append) {
    grid.innerHTML = '';
  }

  if (!articles.length && !append) {
    grid.innerHTML = `
      <div class="empty-state">
        <h3>No articles found</h3>
        <p>Run <code>python fetch_news.py</code> to fetch today's news.</p>
      </div>`;
    return;
  }

  const grouped = groupBySection(articles);

  for (const [section, arts] of Object.entries(grouped)) {
    // If appending and the section group already exists, add cards to it
    let cardsRow;
    let existingGroup = grid.querySelector(`[data-section="${section}"]`);

    if (append && existingGroup) {
      cardsRow = existingGroup.querySelector('.cards-row');
    } else {
      const group = document.createElement('div');
      group.className = 'section-group';
      group.dataset.section = section;

      group.innerHTML = `
        <div class="section-heading">
          <h2>${sectionDisplayName(section)}</h2>
          <span class="section-count">${arts.length} articles</span>
        </div>
        <div class="cards-row"></div>
      `;
      grid.appendChild(group);
      cardsRow = group.querySelector('.cards-row');
    }

    for (const art of arts) {
      cardsRow.appendChild(renderCard(art));
    }
  }
}

// ---- Data fetching ----

async function fetchArticles(reset = false) {
  if (reset) {
    state.offset = 0;
    state.articles = [];
  }

  const params = new URLSearchParams({
    section: state.section,
    days: state.days,
    limit: LIMIT,
    offset: state.offset,
  });

  let data;
  try {
    data = await apiFetch(`/api/articles?${params}`);
  } catch (e) {
    if (reset) {
      document.getElementById('newsGrid').innerHTML = `
        <div class="empty-state">
          <h3>Could not connect to server</h3>
          <p>Make sure <code>python viewer_server.py</code> is running.</p>
        </div>`;
    }
    return;
  }

  const newArticles = data.articles || [];
  state.hasMore = newArticles.length === LIMIT;
  state.offset += newArticles.length;
  state.articles = state.articles.concat(newArticles);

  renderGrid(newArticles, !reset);

  const loadMoreContainer = document.getElementById('loadMoreContainer');
  loadMoreContainer.style.display = state.hasMore ? 'block' : 'none';
}

async function fetchStatus() {
  try {
    const data = await apiFetch('/api/status');
    const el = document.getElementById('statusText');
    const count = data.articles_today || 0;
    const last = data.last_scraped
      ? relativeTime(data.last_scraped)
      : 'never';
    el.textContent = `${count} articles today · last fetched ${last}`;
  } catch {
    // silently ignore
  }
}

// ---- Event handlers ----

function applyFilters() {
  renderGrid([], false); // show loading
  document.getElementById('newsGrid').innerHTML = `
    <div class="loading-state">
      <div class="spinner"></div>
      <p>Loading articles...</p>
    </div>`;
  fetchArticles(true);
}

function initTheme() {
  const saved = localStorage.getItem('theme') || 'light';
  if (saved === 'dark') {
    document.body.classList.add('dark');
    document.getElementById('themeToggle').textContent = '☀️';
  }
}

function toggleTheme() {
  document.body.classList.toggle('dark');
  const isDark = document.body.classList.contains('dark');
  localStorage.setItem('theme', isDark ? 'dark' : 'light');
  document.getElementById('themeToggle').textContent = isDark ? '☀️' : '🌙';
}

// ---- Init ----

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  fetchStatus();
  fetchArticles(true);

  // Theme toggle
  document.getElementById('themeToggle').addEventListener('click', toggleTheme);

  // Section pills
  document.getElementById('sectionPills').addEventListener('click', (e) => {
    const pill = e.target.closest('.pill');
    if (!pill) return;
    document.querySelectorAll('#sectionPills .pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    state.section = pill.dataset.section;
    applyFilters();
  });

  // Day pills
  document.getElementById('dayPills').addEventListener('click', (e) => {
    const pill = e.target.closest('.pill');
    if (!pill) return;
    document.querySelectorAll('#dayPills .pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    state.days = parseInt(pill.dataset.days, 10);
    applyFilters();
  });

  // Load more
  document.getElementById('loadMoreBtn').addEventListener('click', () => {
    fetchArticles(false);
  });
});
