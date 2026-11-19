import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Select, Spin, Table } from 'antd';
import { BrandMark, Chart, Icon } from './components.jsx';
import { chatHistory, integer, money } from './data-utils.js';

const sqlKeywords = new Set('select from where as join left right inner outer on group by order asc desc limit offset distinct count sum avg min max case when then else end and or not null is in like between union all with having date year month'.split(' '));

function sqlTokens(sql) {
  return sql.split(/(--[^\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|"(?:""|[^"])*"|\b[a-z_]+\b)/gi).filter(Boolean).map((text, index) => {
    const kind = text.startsWith('--') || text.startsWith('/*') ? 'comment' : text.startsWith("'") || text.startsWith('"') ? 'string' : sqlKeywords.has(text.toLowerCase()) ? 'keyword' : '';
    return <span className={kind ? `sql-${kind}` : undefined} key={`${index}-${kind}`}>{text}</span>;
  });
}

const prompts = [
  { icon: 'chart', title: '分析交易趋势', question: 'GMV按月的趋势怎么样？', note: '查看每月 GMV 与订单变化' },
  { icon: 'layers', title: '找到领先品类', question: '哪个品类销售额最高？', note: '比较品类的销售贡献' },
  { icon: 'shop', title: '了解卖家表现', question: 'GMV最高的前五个卖家是谁？', note: '找出全周期 GMV 领先卖家' },
  { icon: 'chat', title: '洞察负面评论', question: '低分评论主要在抱怨什么？', note: 'SQL 统计结合评论原文引用' },
];

function ResultChart({ config, rows, datasetConfig }) {
  if (!config || !['line', 'bar', 'pie'].includes(config.type) || !rows.length) return null;
  const category = config.name || config.x;
  const metric = config.value || config.y?.[0];
  if (!category || !metric || !rows.some((row) => row[category] != null && row[metric] != null)) return null;
  const currencyUnit = config.unit === datasetConfig.currency;
  const formatMoney = (value) => money(value, datasetConfig.currency, datasetConfig.currency_locale);
  const format = currencyUnit ? formatMoney : config.unit === 'percent' ? (value) => `${integer(value)}%` : integer;
  const option = config.type === 'pie' ? {
    color: ['#3568e8', '#73a0f1', '#169e91', '#82c9c1', '#9b8bc4', '#c7d7f3'],
    tooltip: { trigger: 'item', renderMode: 'richText', formatter: ({ name, value }) => `${name}\n${format(value)}` },
    series: [{ type: 'pie', radius: ['38%', '68%'], data: rows.map((row) => ({ name: row[category], value: row[metric] })), itemStyle: { borderColor: '#fff', borderWidth: 3 }, label: { color: '#64748b', formatter: ({ name, value }) => `${name} ${format(value)}` } }],
  } : {
    grid: { left: 12, right: 20, top: 24, bottom: 20, containLabel: true },
    tooltip: { trigger: 'axis', renderMode: 'richText', valueFormatter: format },
    xAxis: { type: 'category', data: rows.map((row) => row[category]), axisLabel: { color: '#64748b', rotate: rows.length > 8 ? 35 : 0, hideOverlap: true }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e8edf5' } } },
    yAxis: { type: 'value', axisLabel: { color: '#64748b', formatter: currencyUnit ? (value) => Math.abs(value) >= 1000 ? `${integer(value / 1000)}k` : integer(value) : integer }, splitLine: { lineStyle: { color: '#edf1f7', type: 'dashed' } } },
    series: [{ type: config.type, data: rows.map((row) => row[metric]), showSymbol: false, lineStyle: { color: '#3568e8', width: 2.5 }, itemStyle: { color: '#3568e8', borderRadius: config.type === 'bar' ? [4, 4, 0, 0] : 0 }, areaStyle: config.type === 'line' ? { color: 'rgba(53,104,232,.06)' } : undefined }],
  };
  return <div className="chat-chart"><div className="result-chart-title"><Icon name="chart" size={16} />查询结果图表<span>{currencyUnit ? `金额 / ${datasetConfig.currency}` : config.unit === 'percent' ? '占比 / %' : ''}</span></div><Chart className="chat-chart-inner" option={option} />{config.note && <p className="coverage-note">{config.note}</p>}</div>;
}

function AnswerText({ text, anchorPrefix }) {
  const parts = String(text || '').split(/(\[R\d+\])/g).filter(Boolean);
  return <div className="message-bubble">{parts.map((part, index) => {
    const match = /^\[(R\d+)\]$/.exec(part);
    return match ? <a className="citation-link" href={`#${anchorPrefix}-${match[1]}`} key={`${index}-${part}`}>{part}</a> : <span key={`${index}-plain`}>{part}</span>;
  })}</div>;
}

function ReviewInsight({ insight, anchorPrefix }) {
  const stats = insight?.stats;
  if (!stats) return null;
  const matching = insight.matching_stats || stats;
  const filters = insight.retrieval?.filters || {};
  const hasScoreFilter = 'score' in filters || 'min_score' in filters || 'max_score' in filters;
  const distribution = Array.isArray(stats.score_distribution) ? stats.score_distribution : [];
  const maxCount = Math.max(1, ...distribution.map((item) => Number(item.count) || 0));
  const citations = Array.isArray(insight.citations) ? insight.citations : [];
  const cards = [
    { label: '范围内评价', value: integer(stats.total_reviews), unit: '条', note: `${stats.date_start || '—'} 至 ${stats.date_end || '—'}` },
    { label: '平均评分', value: Number(stats.average_score || 0).toFixed(2), unit: '分', note: `${integer(stats.text_reviews)} 条有文本` },
    { label: '低分评价', value: integer(stats.low_score_reviews), unit: '条', note: `占全部评价 ${Number(stats.low_score_ratio || 0).toFixed(2)}%` },
    { label: hasScoreFilter ? '匹配评价' : '高分评价', value: integer(hasScoreFilter ? matching.total_reviews : stats.positive_score_reviews), unit: '条', note: hasScoreFilter ? `依据评分/范围筛选，${integer(matching.text_reviews)} 条有文本` : '4–5 分评价数' },
  ];
  return <section className="review-insight" aria-label="评论统计与原文引用">
    <div className="review-insight-heading"><span><Icon name="chat" size={15} />评论洞察</span><small>SQL 统计 · 代表性原文引用</small></div>
    <div className="review-stat-grid">{cards.map((card) => <div className="review-stat" key={card.label}><span>{card.label}</span><strong>{card.value}<small>{card.unit}</small></strong><em>{card.note}</em></div>)}</div>
    {distribution.length > 0 && <div className="score-distribution"><div className="score-distribution-heading"><strong>评分分布</strong><span>1–5 分</span></div>{[1, 2, 3, 4, 5].map((score) => {
      const item = distribution.find((entry) => Number(entry.score) === score);
      const count = Number(item?.count) || 0;
      return <div className="score-row" key={score}><span>{score} 分</span><div className="score-track"><i className={score <= 2 ? 'is-low' : score === 3 ? 'is-mid' : 'is-high'} style={{ width: `${Math.round(count / maxCount * 100)}%` }} /></div><b>{integer(count)}</b></div>;
    })}</div>}
    {citations.length > 0 && <details className="review-citations" open><summary>代表性评论原文（{citations.length} 条）</summary><div className="review-citation-list">{citations.map((doc) => <article className="review-citation" id={`${anchorPrefix}-${doc.citation_id}`} key={doc.citation_id}>
      <header><strong>{doc.citation_id}</strong><span className={`review-score ${Number(doc.score) <= 2 ? 'is-low' : Number(doc.score) === 3 ? 'is-mid' : 'is-high'}`}>{doc.score} 分</span><span>{String(doc.created_at || doc.order_date || '').slice(0, 10) || '日期未知'}</span><span>{(doc.categories || []).join('、') || '未关联品类'}</span></header>
      {doc.title && <h4>{doc.title}</h4>}
      <blockquote>{doc.text || '（无正文，仅标题）'}</blockquote>
      <footer><span>评价 <code>{doc.review_id}</code></span><span>订单 <code>{doc.order_id}</code></span></footer>
    </article>)}</div></details>}
  </section>;
}

function ChatMessage({ message, datasetConfig, anchorPrefix }) {
  const [copied, setCopied] = useState(false);
  const data = message.data;
  const rows = data?.rows || [];
  const columns = rows.length ? Object.keys(rows[0]) : [];
  async function copySql() {
    try {
      await navigator.clipboard.writeText(data.sql);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = data.sql;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      const success = document.execCommand('copy');
      textarea.remove();
      setCopied(success);
      if (success) window.setTimeout(() => setCopied(false), 1800);
    }
  }
  return <article className={`message-row ${message.role}`}><div className="message-avatar">{message.role === 'assistant' ? <BrandMark /> : '你'}</div><div className="message-content"><div className="message-author">{message.role === 'assistant' ? 'DataMind' : '你'}<time>{message.time}</time>{data?.sql && <span className="answer-source">数据查询</span>}{data?.review_insight && <span className="answer-source">评论洞察</span>}</div><AnswerText text={message.text} anchorPrefix={anchorPrefix} />
    {data?.chart_config && <ResultChart config={data.chart_config} rows={rows} datasetConfig={datasetConfig} />}
    {data?.review_insight && <ReviewInsight insight={data.review_insight} anchorPrefix={anchorPrefix} />}
    {rows.length > 0 && <details className="result-details" open={data?.response_type === 'table'}><summary>查看结果明细（{rows.length} 行）{data.result_truncated ? ' · 返回数量已达上限' : ''}</summary><Table className="result-table" size="small" rowKey="__rowKey" scroll={{ x: 'max-content' }} pagination={{ pageSize: 8, showSizeChanger: false, hideOnSinglePage: true }} dataSource={rows.map((row, index) => ({ ...row, __rowKey: index }))} columns={columns.map((column) => ({ title: column, dataIndex: column, render: (value) => value == null ? '—' : typeof value === 'number' ? integer(value) : String(value) }))} /></details>}
    {data?.sql && <section className="sql-details"><div className="sql-heading"><span>SQL 查询 · {data.row_count ?? rows.length} 行</span>{(data.execution_time_ms != null || data.execution_time != null) && <span>执行时间 {data.execution_time_ms != null ? `${data.execution_time_ms} ms` : data.execution_time}</span>}<Button size="small" icon={<Icon name="copy" size={14} />} onClick={copySql}>{copied ? '已复制' : '复制 SQL'}</Button></div><pre aria-label="SQL 查询语句"><code>{sqlTokens(data.sql)}</code></pre></section>}
  </div></article>;
}

export default function ChatPage({ active, draft, data, conversation, conversations, storageFailed, onCreateConversation, onSelectConversation, onDeleteConversation, onMessagesChange, onBusyChange }) {
  const datasetConfig = data?.config || { display_name: 'Olist 电商数据', region: 'Brazil', currency: 'BRL', currency_locale: 'zh-CN', currency_label: '巴西雷亚尔' };
  const messages = conversation?.messages || [];
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const latestMessage = useRef(null);
  const input = useRef(null);
  const sending = useRef(false);

  useEffect(() => { if (draft) { setQuestion(draft.text); input.current?.focus(); } }, [draft]);
  useEffect(() => { setQuestion(''); setError(null); }, [conversation?.id]);
  useEffect(() => {
    if (active && (messages.length || busy)) latestMessage.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'nearest' });
  }, [active, messages, busy, error]);

  async function send(text = question) {
    const content = text.trim();
    if (!content || sending.current) return;
    sending.current = true;
    setQuestion(''); setError(null); setBusy(true); onBusyChange(true);
    const history = chatHistory(messages);
    const conversationId = conversation.id;
    const time = () => new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    onMessagesChange(conversationId, (current) => [...current, { role: 'user', text: content, time: time() }]);
    try {
      const response = await fetch('/api/chat', { method: 'POST', signal: AbortSignal.timeout(180000), headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: content, chat_history: history }) });
      const body = await response.json();
      const result = body.data || null;
      if (!response.ok || body.code !== 200 || result?.error_category) {
        const category = result?.error_category;
        const labels = { sql: 'SQL 错误', connection: '数据库连接错误', configuration: '数据库配置错误', guard: '安全策略错误', unsupported: '暂不支持', review_index: '评论索引错误' };
        const detail = category === 'sql' ? result?.sql_error : null;
        setQuestion(content);
        setError({ title: labels[category] || (response.status >= 500 ? '服务错误' : '业务错误'), message: detail || result?.answer || body.message || '请求未能完成。' });
        return;
      }
      onMessagesChange(conversationId, (current) => [...current, { role: 'assistant', text: result?.answer || body.message || '没有收到可显示的回答。', time: time(), data: result }]);
    } catch (err) {
      setQuestion(content);
      const timedOut = err.name === 'TimeoutError' || err.name === 'AbortError';
      const networkError = err instanceof TypeError;
      setError({ title: timedOut ? '网络超时' : networkError ? '网络错误' : '服务响应错误', message: timedOut ? '查询请求超时，请缩小问题范围后重试。' : networkError ? '无法连接问数服务，请检查网络和后端服务后重试。' : '问数服务返回了无法识别的响应，请稍后重试。' });
    } finally { sending.current = false; setBusy(false); onBusyChange(false); }
  }

  return <div className="chat-layout"><section className="chat-main"><div className="chat-heading"><div><h1>智能问数</h1><p>提问、探索，让数据回答。</p></div><div className="chat-heading-actions">
    {conversations.length > 1 && <Select className="conversation-select" aria-label="选择历史对话" value={conversation?.id} options={conversations.map((chat) => ({ value: chat.id, label: chat.title }))} onChange={onSelectConversation} disabled={busy} />}
    <Button className="conversation-delete-current" icon={<Icon name="trash" size={16} />} aria-label="删除当前对话" title="删除当前对话" danger disabled={busy} onClick={onDeleteConversation} />
    <Button icon={<Icon name="plus" size={16} />} disabled={busy} onClick={onCreateConversation}>新对话</Button>
  </div></div>
    {storageFailed && <Alert className="chat-storage-alert" type="warning" showIcon message="浏览器本地存储不可用或空间不足，对话无法保证在刷新后保留。" />}
    {!messages.length ? <div className="chat-welcome"><span className="welcome-symbol"><Icon name="spark" size={28} /></span><h2>从一个好问题开始</h2><p>用自然语言探索{datasetConfig.display_name}的交易、品类、卖家和评论数据。<br />指标答案查看查询结果，评论洞察查看统计与原文引用。</p><div className="prompt-grid">{prompts.map((prompt) => <button key={prompt.title} onClick={() => send(prompt.question)} disabled={busy}><Icon name={prompt.icon} /><strong>{prompt.title}</strong><span>{prompt.note}</span><Icon name="arrow" size={16} /></button>)}</div></div> : <section className="conversation" aria-live="polite" aria-label="对话记录">{messages.map((message, index) => <ChatMessage key={index} message={message} datasetConfig={datasetConfig} anchorPrefix={`chat-message-${index}`} />)}{busy && <div className="thinking"><Spin size="small" /><span>正在查询数据…</span></div>}{error && <Alert type="error" showIcon message={error.title} description={error.message} />}<div ref={latestMessage} /></section>}
    <div className="composer-wrap"><form className="composer" onSubmit={(event) => { event.preventDefault(); send(); }}><Input.TextArea ref={input} aria-label="输入经营数据问题" value={question} onChange={(event) => setQuestion(event.target.value)} onPressEnter={(event) => { if (!event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} placeholder="例如：低分评论主要在抱怨什么？或 GMV 按月趋势怎么样？" autoSize={{ minRows: 2, maxRows: 5 }} maxLength={500} disabled={busy} /><div className="composer-bottom"><span><Icon name="database" size={14} />{datasetConfig.display_name} 指标与评论快照</span><Button htmlType="submit" type="primary" icon={<Icon name="send" size={16} />} disabled={!question.trim() || busy} loading={busy}>发送</Button></div></form><p className="composer-note">Enter 发送，Shift + Enter 换行 · 仅查询授权数据，无法计算的指标会明确说明</p></div>
  </section><aside className="chat-context"><div className="context-heading"><Icon name="database" /><h2>分析上下文</h2></div><div className="context-source"><span className="source-icon">{datasetConfig.currency}</span><div><strong>{datasetConfig.display_name}</strong><span>{datasetConfig.region}</span></div></div><dl><div><dt>数据覆盖</dt><dd>{data ? `${data.overview.data_period_start} 至 ${data.overview.data_period_end}` : '暂未读取到覆盖期'}</dd></div><div><dt>金额单位</dt><dd>{datasetConfig.currency} · {datasetConfig.currency_label}</dd></div><div><dt>可探索主题</dt><dd><span className="topic-chip">GMV</span><span className="topic-chip">订单</span><span className="topic-chip">品类</span><span className="topic-chip">卖家</span><span className="topic-chip">评论</span></dd></div></dl><div className="context-note"><Icon name="info" size={17} /><p>GMV口径：{datasetConfig.gmv_definition}。实际覆盖期以当前汇总数据为准。</p></div><div className="context-questions"><h3>也可以这样问</h3>{['低分评论主要在抱怨什么？', '物流破损相关评论有哪些？'].map((text) => <button key={text} disabled={busy} onClick={() => { setQuestion(text); input.current?.focus(); }}>{text}<Icon name="chevron" size={14} /></button>)}</div></aside></div>;
}
