/** Multi-agent chat widget — LangGraph supervisor team with live SSE events */
const MultiAgentChat = {
  conversationId: null,
  open: false,
  persona: 'customer',

  AGENTS_CUSTOMER: [
    'supervisor', 'shopping', 'product', 'order', 'checkout',
    'policy', 'refund', 'support', 'recommendation',
  ],
  AGENTS_OWNER: [
    'supervisor', 'admin', 'order', 'product', 'support', 'policy',
  ],

  SUGGEST_CUSTOMER: [
    'Find laptops under ₹80000 with 16GB RAM',
    'Compare phones with rating 4.5+',
    'Show ANC headphones under ₹30000',
    'Where is my latest order?',
  ],
  SUGGEST_OWNER: [
    'List pending orders',
    'Approve the oldest pending order',
    'Summarize today’s order queue',
    'Which SKUs need restock?',
  ],

  init(options = {}) {
    this.persona = options.persona || (localStorage.getItem('is_admin') === 'true' ? 'owner' : 'customer');
    if (document.getElementById('cm-chat-root')) return;
    const root = document.createElement('div');
    root.id = 'cm-chat-root';
    root.innerHTML = this._markup();
    document.body.appendChild(root);
    this._bind();
    this._renderAgents();
    this._renderSuggestions();
    this._welcome();
  },

  _agents() {
    return this.persona === 'owner' ? this.AGENTS_OWNER : this.AGENTS_CUSTOMER;
  },

  _suggestions() {
    return this.persona === 'owner' ? this.SUGGEST_OWNER : this.SUGGEST_CUSTOMER;
  },

  _markup() {
    const title = this.persona === 'owner' ? 'Owner Concierge' : 'Jewellery Concierge';
    const sub = 'LangGraph multi-agent · live handoffs';
    return `
      <button type="button" class="cm-chat-fab" id="cm-chat-fab" aria-label="Open AI chat">
        <i class="fas fa-comments"></i>
      </button>
      <div class="cm-chat-panel" id="cm-chat-panel" role="dialog" aria-label="${title}">
        <div class="cm-chat-head">
          <div>
            <strong>${title}</strong>
            <small>${sub}</small>
          </div>
          <button type="button" id="cm-chat-close" style="background:none;border:none;color:#fff;cursor:pointer;font-size:18px" aria-label="Close">×</button>
        </div>
        <div class="cm-chat-agents" id="cm-chat-agents"></div>
        <div class="cm-chat-msgs" id="cm-chat-msgs"></div>
        <div class="cm-suggest" id="cm-chat-suggest"></div>
        <div class="cm-chat-input">
          <input type="text" id="cm-chat-input" placeholder="Ask the LangGraph team…" autocomplete="off">
          <button type="button" id="cm-chat-send"><i class="fas fa-paper-plane"></i></button>
        </div>
      </div>`;
  },

  _bind() {
    document.getElementById('cm-chat-fab').onclick = () => this.toggle();
    document.getElementById('cm-chat-close').onclick = () => this.toggle(false);
    document.getElementById('cm-chat-send').onclick = () => this.send();
    document.getElementById('cm-chat-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') this.send();
    });
  },

  _renderAgents() {
    const el = document.getElementById('cm-chat-agents');
    el.innerHTML = this._agents().map(a => `<span class="cm-chip" data-agent="${a}">${a}</span>`).join('');
  },

  _renderSuggestions() {
    const el = document.getElementById('cm-chat-suggest');
    el.innerHTML = this._suggestions()
      .map(s => `<button type="button" data-q="${s.replace(/"/g, '&quot;')}">${s}</button>`)
      .join('');
    el.querySelectorAll('button').forEach(btn => {
      btn.onclick = () => {
        document.getElementById('cm-chat-input').value = btn.dataset.q;
        this.send();
      };
    });
  },

  _welcome() {
    const text = this.persona === 'owner'
      ? 'LangGraph supervisor coordinates admin, order, product, and policy agents for ShopZone.'
      : 'LangGraph shopping agents search phones, laptops, audio & more by price, rating, and features from the live catalogue.';
    this._appendBot(text, { entry_agent: 'supervisor', mode: 'multi_agent', framework: 'langgraph' });
  },

  toggle(force) {
    this.open = typeof force === 'boolean' ? force : !this.open;
    document.getElementById('cm-chat-panel').classList.toggle('open', this.open);
    if (this.open) document.getElementById('cm-chat-input').focus();
  },

  _append(role, text, meta) {
    const box = document.getElementById('cm-chat-msgs');
    const div = document.createElement('div');
    div.className = `cm-msg ${role}`;
    div.textContent = text;
    if (meta) {
      const m = document.createElement('span');
      m.className = 'meta';
      m.textContent = meta;
      div.appendChild(m);
    }
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
  },

  _appendBot(text, data = {}) {
    const parts = [];
    if (data.framework) parts.push(data.framework);
    if (data.mode) parts.push(data.mode);
    if (data.entry_agent) parts.push(`via ${data.entry_agent}`);
    if (data.sub_results?.length) {
      parts.push(data.sub_results.map(s => s.agent || s).join(' → '));
    }
    this._append('bot', text, parts.join(' · '));
    this._highlightAgents(data);
  },

  _highlightAgents(data) {
    const active = new Set();
    if (data.entry_agent) active.add(data.entry_agent);
    if (data.agent) active.add(data.agent);
    (data.sub_results || []).forEach(s => {
      if (typeof s === 'string') active.add(s);
      else if (s?.agent) active.add(s.agent);
    });
    (data.agents || []).forEach(a => active.add(a));
    document.querySelectorAll('#cm-chat-agents .cm-chip').forEach(chip => {
      chip.classList.toggle('active', active.has(chip.dataset.agent));
    });
  },

  _authHeaders() {
    return (typeof Shop !== 'undefined')
      ? Shop.getAuthHeaders(true)
      : {
          'Content-Type': 'application/json',
          Authorization: `Bearer customer:${localStorage.getItem('customer_id') || 'CU-1001'}`,
        };
  },

  async send() {
    const input = document.getElementById('cm-chat-input');
    const message = (input.value || '').trim();
    if (!message) return;
    input.value = '';
    this._append('user', message);
    document.getElementById('cm-chat-suggest').innerHTML = '';

    const thinking = this._append('bot', 'LangGraph supervisor planning…', 'langgraph · live');
    thinking.id = 'cm-chat-thinking';

    try {
      await this._sendStream(message, thinking);
    } catch (err) {
      thinking.remove();
      try {
        await this._sendFallback(message);
      } catch (e2) {
        this._appendBot('Sorry — ' + (e2.message || err.message || 'chat failed'), {});
      }
    }
  },

  async _sendStream(message, thinkingEl) {
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: this._authHeaders(),
      body: JSON.stringify({
        message,
        mode: 'multi_agent',
        conversation_id: this.conversationId || undefined,
        learn: this.persona === 'customer',
      }),
    });
    if (!res.ok || !res.body) throw new Error('Stream unavailable');

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalPayload = null;
    let eventName = 'message';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() || '';
      for (const chunk of parts) {
        const lines = chunk.split('\n');
        let dataLine = '';
        for (const line of lines) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim();
          if (line.startsWith('data:')) dataLine += line.slice(5).trim();
        }
        if (!dataLine) continue;
        let data;
        try { data = JSON.parse(dataLine); } catch { continue; }

        if (eventName === 'agent') {
          this._onAgentEvent(data, thinkingEl);
        } else if (eventName === 'final') {
          finalPayload = data;
        } else if (eventName === 'error') {
          throw new Error(data.message || 'Stream error');
        }
      }
    }

    thinkingEl.remove();
    if (!finalPayload) throw new Error('No final answer');
    this.conversationId = finalPayload.conversation_id || this.conversationId;
    const sub = (finalPayload.sub_results || []).map(s =>
      typeof s === 'string' ? s : `${s.agent}${s.summary ? ': ' + String(s.summary).slice(0, 80) : ''}`
    );
    let answer = finalPayload.answer || '(No answer)';
    if (sub.length) {
      answer += `\n\n— LangGraph specialists —\n` + sub.map(s => `• ${s}`).join('\n');
    }
    this._appendBot(answer, finalPayload);
  },

  _onAgentEvent(data, thinkingEl) {
    if (data.type === 'graph_start') {
      thinkingEl.firstChild
        ? (thinkingEl.childNodes[0].textContent = 'LangGraph graph started…')
        : (thinkingEl.textContent = 'LangGraph graph started…');
    }
    if (data.type === 'supervisor' && data.agent) {
      thinkingEl.childNodes[0]
        ? (thinkingEl.childNodes[0].textContent = `Delegating → ${data.agent}`)
        : (thinkingEl.textContent = `Delegating → ${data.agent}`);
      this._highlightAgents({ agent: data.agent, entry_agent: 'supervisor' });
    }
    if (data.type === 'agent_start') {
      thinkingEl.childNodes[0]
        ? (thinkingEl.childNodes[0].textContent = `${data.agent} running…`)
        : (thinkingEl.textContent = `${data.agent} running…`);
      this._highlightAgents({ agent: data.agent, entry_agent: 'supervisor' });
    }
    if (data.type === 'agent_done') {
      this._append('bot', `${data.agent}: ${(data.summary || '').slice(0, 160)}`, 'langgraph · specialist');
      this._highlightAgents({ agent: data.agent });
    }
  },

  async _sendFallback(message) {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: this._authHeaders(),
      body: JSON.stringify({
        message,
        mode: 'multi_agent',
        conversation_id: this.conversationId || undefined,
        learn: this.persona === 'customer',
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.message || 'Chat failed');
    this.conversationId = data.conversation_id || this.conversationId;
    const sub = (data.sub_results || []).map(s =>
      typeof s === 'string' ? s : `${s.agent}${s.summary ? ': ' + String(s.summary).slice(0, 80) : ''}`
    );
    let answer = data.answer || '(No answer)';
    if (sub.length) {
      answer += `\n\n— Specialists —\n` + sub.map(s => `• ${s}`).join('\n');
    }
    this._appendBot(answer, data);
  },
};

document.addEventListener('DOMContentLoaded', () => {
  const path = location.pathname;
  if (path.includes('/shop/login')) return;
  const persona = path.includes('/shop/admin') ? 'owner' : 'customer';
  MultiAgentChat.init({ persona });
});
