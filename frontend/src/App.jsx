import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Modal, Spin } from 'antd';
import { BrandMark, Icon } from './components.jsx';
import { Dashboard, RankingPage } from './Dashboard.jsx';
import ChatPage from './ChatPage.jsx';
import SkeletonDashboard from './SkeletonDashboard.jsx';
import { createConversation, deleteConversation, loadChatState, saveChatState, updateConversation } from './chat-state.js';

const pages = { dashboard: '经营总览', categories: '品类分析', sellers: '卖家分析', chat: '智能问数' };
const navigation = [['dashboard', 'overview'], ['chat', 'spark'], ['categories', 'layers'], ['sellers', 'shop']];
const DEFAULT_CONFIG = { display_name: 'Olist 电商数据', region: 'Brazil', currency: 'BRL', currency_symbol: 'R$', currency_locale: 'zh-CN', currency_label: '巴西雷亚尔', timezone: 'UTC', gmv_definition: '商品金额 + 运费' };

function dashboardDefaults(config) { return { ...DEFAULT_CONFIG, ...config }; }

async function getMetric(name) {
  const response = await fetch(`/api/metrics/${name}`, { signal: AbortSignal.timeout(20000) });
  const body = await response.json();
  if (!response.ok || body.code !== 200) throw new Error(body.message || `请求失败 (${response.status})`);
  return body.data;
}

export default function App() {
  const [page, setPage] = useState('dashboard');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [definitions, setDefinitions] = useState(false);
  const [draft, setDraft] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [chatState, setChatState] = useState(loadChatState);
  const [chatBusy, setChatBusy] = useState(false);
  const [chatStorageFailed, setChatStorageFailed] = useState(false);
  const [chatToDelete, setChatToDelete] = useState(null);
  const activeConversation = chatState.conversations.find((chat) => chat.id === chatState.activeId);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [overview, trend, categories, sellers, config] = await Promise.all([
        getMetric('overview'), getMetric('trend'), getMetric('category-rank'), getMetric('top-sellers'), getMetric('config'),
      ]);
      setData({ overview, trend, categories, sellers, config: dashboardDefaults(config) });
      setLastUpdated(new Date());
    } catch (err) { setError(err.message || '暂时无法读取经营数据'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => { setChatStorageFailed(!saveChatState(chatState)); }, [chatState]);

  function navigate(nextPage) { setPage(nextPage); window.scrollTo({ top: 0 }); }
  function ask(question) { setDraft({ text: question, id: Date.now() }); navigate('chat'); }
  function createNewConversation() {
    if (chatBusy) return;
    const conversation = createConversation();
    setChatState((current) => ({ conversations: [conversation, ...current.conversations], activeId: conversation.id }));
    setDraft(null);
  }
  function selectConversation(id) { if (chatBusy) return; setChatState((current) => ({ ...current, activeId: id })); setDraft(null); }
  function confirmDeleteConversation() {
    if (chatBusy || !chatToDelete) return;
    const { id } = chatToDelete;
    setChatState((current) => deleteConversation(current, id));
    if (id === chatState.activeId) setDraft(null);
    setChatToDelete(null);
  }
  function updateMessages(id, messages) {
    setChatState((current) => {
      const conversation = current.conversations.find((chat) => chat.id === id);
      if (!conversation) return current;
      const nextMessages = typeof messages === 'function' ? messages(conversation?.messages || []) : messages;
      return updateConversation(current, id, nextMessages);
    });
  }

  return <div className="workspace">
    <a className="skip-link" href="#main-content">跳到主要内容</a>
    <aside className="sidebar">
      <a className="brand" href="#" onClick={(event) => { event.preventDefault(); navigate('dashboard'); }} aria-label="DataMind 首页"><BrandMark /><span>DataMind<small>数据分析工作台</small></span></a>
      <div className="workspace-label"><span className="dataset-symbol"><Icon name="database" /></span><div><strong>{data?.config.display_name || DEFAULT_CONFIG.display_name}</strong><small>{data?.config.region || DEFAULT_CONFIG.region}</small></div><span className="dataset-tag">{data?.config.currency || DEFAULT_CONFIG.currency}</span></div>
      <p className="nav-caption">工作空间</p>
      <nav aria-label="主导航">{navigation.map(([key, icon]) => <button key={key} aria-label={pages[key]} className={`nav-item ${page === key ? 'selected' : ''}`} aria-current={page === key ? 'page' : undefined} onClick={() => navigate(key)}><Icon name={icon} /><span>{pages[key]}</span>{key === 'chat' && <span className="ai-label">AI</span>}</button>)}</nav>
      {page === 'chat' && <section className="conversation-nav" aria-label="已保存的对话">
        <div className="conversation-nav-heading">对话历史<span>{chatState.conversations.length}</span></div>
        <div className="conversation-nav-list">{chatState.conversations.map((chat) => <div className="conversation-nav-row" key={chat.id}>
          <button type="button" className={`conversation-nav-item ${chat.id === chatState.activeId ? 'active' : ''}`} disabled={chatBusy} aria-current={chat.id === chatState.activeId ? 'true' : undefined} title={chat.title} onClick={() => selectConversation(chat.id)}><Icon name="chat" size={15} /><span>{chat.title}</span></button>
          <button type="button" className="conversation-delete" disabled={chatBusy} aria-label={`删除对话：${chat.title}`} title="删除对话" onClick={() => setChatToDelete(chat)}><Icon name="trash" size={15} /></button>
        </div>)}</div>
      </section>}
      <div className="sidebar-bottom"><button className="definitions-button" onClick={() => setDefinitions(true)}><Icon name="info" /> 指标口径</button><div className="workspace-footer"><span className="workspace-avatar">DM</span><div><strong>DataMind 工作空间</strong><small>只读数据分析</small></div></div></div>
    </aside>

    <div className="workspace-main">
      <header className="workspace-topbar"><div className="breadcrumb"><span>工作空间</span><Icon name="chevron" /><strong>{pages[page]}</strong></div><div className="topbar-meta"><span className={`data-status ${error ? 'error' : loading ? 'pending' : ''}`}><i />{error ? '读取异常' : loading ? '读取中' : '数据读取正常'}</span><span className="topbar-source">{data ? `${data.config.display_name} / ${data.config.region}` : DEFAULT_CONFIG.display_name}</span></div></header>
      <main id="main-content" className={`main-content ${page === 'chat' ? 'is-chat' : ''}`}>
        {page !== 'chat' && <div className="page-heading"><div><h1>{pages[page]}</h1><p>{page === 'dashboard' ? '从成交趋势到经营细节，让每一次分析有据可依。' : page === 'categories' ? '比较品类表现，查看全周期交易贡献。' : '按全周期交易额排序，查看领先卖家的经营表现。'}</p></div><div className="heading-actions"><div className="date-range"><Icon name="calendar" /><span><small>数据覆盖</small>{data ? `${data.overview.data_period_start} — ${data.overview.data_period_end}` : '正在读取日期范围'}</span></div>{page === 'dashboard' && <span className="last-updated">最后更新时间：{lastUpdated ? lastUpdated.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : '—'}</span>}<Button className="refresh-button" icon={<Icon name="refresh" />} loading={loading} onClick={refresh} aria-label="刷新数据" /></div></div>}
        {error && <Alert className="load-alert" type="error" showIcon title="暂时无法读取看板数据" description={`${error}。请确认后端服务运行后重试。${data ? '当前显示上次读取的数据。' : ''}`} action={<Button onClick={refresh} size="small">重试</Button>} />}
        {page !== 'chat' && loading && !data && <SkeletonDashboard />}
        {data && page === 'dashboard' && <Dashboard data={data} onNavigate={navigate} onAsk={ask} />}
        {data && ['categories', 'sellers'].includes(page) && <RankingPage key={page} kind={page} data={data} onAsk={ask} />}
        <div hidden={page !== 'chat'}><ChatPage active={page === 'chat'} draft={draft} data={data} conversation={activeConversation} conversations={chatState.conversations} storageFailed={chatStorageFailed} onCreateConversation={createNewConversation} onSelectConversation={selectConversation} onDeleteConversation={() => setChatToDelete(activeConversation)} onMessagesChange={updateMessages} onBusyChange={setChatBusy} /></div>
        {page !== 'chat' && data && <footer className="page-footer"><span><Icon name="database" /> {data.config.display_name} · 全周期汇总</span><span>金额单位 {data.config.currency}（{data.config.currency_label}）</span></footer>}
      </main>
    </div>
    <Modal title="删除对话" open={!!chatToDelete} okText="删除" cancelText="取消" okButtonProps={{ danger: true, disabled: chatBusy }} onOk={confirmDeleteConversation} onCancel={() => setChatToDelete(null)}>
      <p>确定删除「{chatToDelete?.title}」吗？删除后无法恢复。</p>
    </Modal>
    <Modal title="指标口径" open={definitions} onCancel={() => setDefinitions(false)} footer={<Button type="primary" onClick={() => setDefinitions(false)}>知道了</Button>}><div className="definition-list"><p><strong>GMV</strong>{data?.config.gmv_definition || DEFAULT_CONFIG.gmv_definition}，币种为 {data?.config.currency || DEFAULT_CONFIG.currency}。</p><p><strong>有效订单</strong>当前汇总表的全周期去重订单数；不可将各品类订单数直接相加。</p><p><strong>下单客户</strong>全周期去重下单客户数；请结合公司客户标识口径解读。</p><p><strong>平均客单价</strong>GMV ÷ 有效订单数。</p><p><strong>筛选范围</strong>总览核心指标和排行均为全周期；趋势图的年份筛选仅影响该图。首末月份可能只覆盖部分日期。</p></div></Modal>
  </div>;
}
