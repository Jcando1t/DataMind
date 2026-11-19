import { useState } from 'react';
import { Input, Table, Tooltip } from 'antd';
import { Chart, Icon, PanelHeading } from './components.jsx';
import { integer, money, partialLastMonth, searchRanking, trendRows } from './data-utils.js';

const metrics = { gmv: 'GMV', orders: '订单数', aov: '客单价' };
const compact = (value) => Number(value) >= 1000000 ? `${(value / 1000000).toFixed(1)}M` : Number(value) >= 1000 ? `${(value / 1000).toFixed(0)}k` : integer(value);
const categoryName = (name) => (name || 'unknown').replaceAll('_', ' ');
const rankCell = (rank) => <span className={`rank-number ${rank <= 3 ? 'leading-rank' : ''}`}>{String(rank).padStart(2, '0')}</span>;

function sellerColumns(formatMoney, currency) {
  return [
    { title: '排名', dataIndex: 'rank', width: 62, render: rankCell },
    { title: '卖家', dataIndex: 'seller_id', render: (id) => <Tooltip title={id}><span className="seller-id">{id.slice(0, 10)}…</span></Tooltip> },
    { title: '所在地区', dataIndex: 'seller_city', render: (city, row) => <span className="seller-location">{city}<small>{row.seller_state}</small></span> },
    { title: '订单数', dataIndex: 'total_orders', align: 'right', render: integer },
    { title: `GMV / ${currency}`, dataIndex: 'total_gmv', align: 'right', render: formatMoney },
  ];
}

export function Dashboard({ data, onNavigate, onAsk }) {
  const { currency, currency_locale: locale } = data.config;
  const formatMoney = (value) => money(value, currency, locale);
  const [year, setYear] = useState('all');
  const [metric, setMetric] = useState('gmv');
  const rows = trendRows(data.trend, year, metric);
  const years = [...new Set(data.trend.map((row) => row.year_month.slice(0, 4)))];
  const peak = data.trend.reduce((max, row) => !max || Number(row.monthly_gmv) > Number(max.monthly_gmv) ? row : max, null);
  const topFiveRatio = data.overview.total_gmv > 0 ? data.categories.slice(0, 5).reduce((sum, row) => sum + Number(row.total_gmv), 0) / data.overview.total_gmv * 100 : null;
  const amountSelected = metric !== 'orders';
  const cards = [
    { label: '商品交易总额', value: formatMoney(data.overview.total_gmv), icon: 'money', note: data.config.gmv_definition, tag: 'GMV', accent: 'blue' },
    { label: '有效订单数', value: integer(data.overview.total_orders), icon: 'orders', note: '全周期去重订单', tag: '订单', accent: 'cyan' },
    { label: '下单客户数', value: integer(data.overview.total_customers), icon: 'users', note: '全周期去重客户', tag: '客户', accent: 'violet' },
    { label: '平均客单价', value: formatMoney(data.overview.avg_order_value), icon: 'average', note: 'GMV ÷ 有效订单数', tag: `${currency} / 订单`, accent: 'amber' },
  ];

  return <>
    <section className="metric-grid" aria-label="全周期核心指标">{cards.map((card) => <article className={`metric-card ${card.accent}`} key={card.label}><div className="metric-heading"><span>{card.label}</span><span className="metric-icon"><Icon name={card.icon} /></span></div><strong className="metric-value">{card.value}</strong><div className="metric-description"><span>{card.note}</span><span>{card.tag}</span></div></article>)}</section>

    <div className="analysis-grid">
      <section className="panel trend-panel" aria-label="交易趋势">
        <PanelHeading title="交易趋势" note="观察成交规模与订单变化" action={<div className="chart-range"><label htmlFor="trend-year">趋势年份</label><select id="trend-year" value={year} onChange={(event) => setYear(event.target.value)}><option value="all">全部年份</option>{years.map((value) => <option key={value} value={value}>{value} 年</option>)}</select></div>} />
        <div className="trend-toolbar"><div className="metric-tabs" aria-label="趋势指标">{Object.entries(metrics).map(([key, label]) => <button key={key} onClick={() => setMetric(key)} aria-pressed={metric === key} className={metric === key ? 'active' : ''}>{label}</button>)}</div><span className="chart-unit">{amountSelected ? `金额 / ${currency}` : '数量 / 单'}</span></div>
        <div className="chart-region" role="img" aria-label={`${year === 'all' ? '全周期' : year + ' 年'}月度${metrics[metric]}趋势，共 ${rows.length} 个月`}><Chart className="trend-chart" option={{
          grid: { left: 12, right: 16, top: 22, bottom: 16, containLabel: true },
          tooltip: { trigger: 'axis', renderMode: 'richText', backgroundColor: '#fff', borderColor: '#e2e8f0', textStyle: { color: '#18243b' }, valueFormatter: amountSelected ? formatMoney : integer },
          xAxis: { type: 'category', data: rows.map((row) => row.year_month), boundaryGap: false, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e8edf5' } }, axisLabel: { color: '#8290a5', fontSize: 11, hideOverlap: true, margin: 16, formatter: (value) => value.slice(2).replace('-', '/') } },
          yAxis: { type: 'value', axisLabel: { color: '#8290a5', fontSize: 11, formatter: compact }, splitLine: { lineStyle: { color: '#edf1f7', type: 'dashed' } } },
          series: [{ name: metrics[metric], type: 'line', data: rows.map((row) => row.value), showSymbol: rows.length < 13, symbolSize: 5, smooth: false, lineStyle: { color: '#3568e8', width: 2.6 }, itemStyle: { color: '#3568e8', borderColor: '#fff', borderWidth: 2 }, areaStyle: { color: 'rgba(53,104,232,.07)' } }],
        }} /></div>
        <div className="chart-footnote"><span><i />{rows.length} 个月度记录</span><span>年份筛选仅作用于本图</span></div>
        {((rows.some((row) => row.year_month === data.overview.data_period_start.slice(0, 7)) && data.overview.data_period_start.slice(-2) !== '01') || (rows.some((row) => row.year_month === data.overview.data_period_end.slice(0, 7)) && partialLastMonth(data.overview.data_period_end))) && <p className="coverage-note"><Icon name="info" size={14} />首末月份按实际覆盖日期统计，部分月份不宜直接比较。</p>}
      </section>

      <section className="panel category-panel"><PanelHeading title="品类贡献" note="全周期 GMV 前 5" action={<button className="text-button" onClick={() => onNavigate('categories')} aria-label="查看全部品类">查看全部<Icon name="chevron" size={14} /></button>} />
        <div className="category-bars">{data.categories.slice(0, 5).map((row, index) => <button key={row.category_english} className="category-entry" onClick={() => onAsk(`${row.category_english}品类的GMV和订单数是多少？`)}><span className="category-label"><span><b>{String(index + 1).padStart(2, '0')}</b>{categoryName(row.category_english)}</span><strong>{Number(row.gmv_ratio).toFixed(2)}%</strong></span><span className="category-track"><i style={{ width: `${Math.min(100, Number(row.total_gmv) / Number(data.categories[0]?.total_gmv || 1) * 100)}%` }} /></span><span className="category-amount">{formatMoney(row.total_gmv)}</span></button>)}</div>
        <p className="panel-footnote">占比以全周期总 GMV 为分母</p>
      </section>

      <section className="panel sellers-panel"><PanelHeading title="领先卖家" note="全周期 GMV 排行前 5" action={<button className="text-button" onClick={() => onNavigate('sellers')}>查看 Top 20<Icon name="chevron" size={14} /></button>} /><Table className="business-table" size="middle" scroll={{ x: 570 }} pagination={false} rowKey="seller_id" dataSource={data.sellers.slice(0, 5)} columns={sellerColumns(formatMoney, currency)} /></section>

      <section className="insights-panel"><PanelHeading title="数据速览" note="从当前汇总结果中发现线索" /><dl><div><dt><Icon name="layers" />有交易的品类</dt><dd>{data.categories.length}<small> 个</small></dd></div><div><dt><Icon name="chart" />前 5 品类 GMV 占比</dt><dd>{topFiveRatio == null ? '—' : `${topFiveRatio.toFixed(2)}%`}</dd></div><div><dt><Icon name="calendar" />GMV 峰值月份</dt><dd className="peak-value">{peak?.year_month || '—'}<small>{peak ? formatMoney(peak.monthly_gmv) : ''}</small></dd></div></dl><div className="ask-inset"><span className="ask-inset-icon"><Icon name="spark" /></span><div><strong>从数据，继续追问</strong><p>趋势、品类或卖家，用一句话深入分析。</p></div><button aria-label="打开智能问数" onClick={() => onAsk('GMV按月的趋势怎么样？')}><Icon name="arrow" /></button></div></section>
    </div>
  </>;
}

export function RankingPage({ kind, data, onAsk }) {
  const [query, setQuery] = useState('');
  const categories = kind === 'categories';
  const source = categories ? data.categories : data.sellers;
  const { currency, currency_locale: locale } = data.config;
  const formatMoney = (value) => money(value, currency, locale);
  const rows = searchRanking(source, query, categories ? ['category_english'] : ['seller_id', 'seller_city', 'seller_state']);
  const columns = categories ? [
    { title: '排名', dataIndex: 'rank', width: 70, render: rankCell },
    { title: '品类', dataIndex: 'category_english', render: (value) => <span className="category-name">{categoryName(value)}</span> },
    { title: '订单数', dataIndex: 'total_orders', align: 'right', render: integer },
    { title: `GMV / ${currency}`, dataIndex: 'total_gmv', align: 'right', render: formatMoney },
    { title: 'GMV 占比', dataIndex: 'gmv_ratio', align: 'right', render: (value) => `${Number(value).toFixed(2)}%` },
  ] : sellerColumns(formatMoney, currency);

  return <section className="panel ranking-panel"><PanelHeading title={categories ? '全品类表现' : '卖家 Top 20'} note={categories ? '同一订单可能包含多个品类，品类订单数不可直接相加。' : '当前接口提供全周期 GMV 最高的 20 位卖家。'} /><div className="ranking-toolbar"><span>共 <strong>{source.length}</strong> {categories ? '个品类' : '位卖家'}{query && `，匹配 ${rows.length} 条`}</span><Input allowClear prefix={<Icon name="search" size={16} />} placeholder={categories ? '搜索品类名称' : '搜索卖家、城市或州'} aria-label={categories ? '搜索品类' : '搜索卖家'} value={query} onChange={(event) => setQuery(event.target.value)} /></div><Table className="business-table" rowKey={categories ? 'category_english' : 'seller_id'} scroll={{ x: 680 }} dataSource={rows} columns={[...columns, { title: '分析', key: 'analyze', width: 95, render: (_, row) => <button className="text-button" onClick={() => onAsk(categories ? `${row.category_english}品类的GMV和订单数是多少？` : `卖家${row.seller_id}的GMV和订单数是多少？`)}>问数<Icon name="spark" size={14} /></button> }]} locale={{ emptyText: '没有匹配结果，请尝试其他关键词' }} pagination={{ pageSize: 10, showSizeChanger: false, hideOnSinglePage: true }} /></section>;
}
