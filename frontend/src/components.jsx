import ReactECharts from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { LineChart, BarChart, PieChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, MarkPointComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

echarts.use([LineChart, BarChart, PieChart, GridComponent, TooltipComponent, MarkPointComponent, CanvasRenderer]);

const icons = {
  overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3 M20 3v4 M18 5h4',
  layers: 'm12 3 10 5-10 5L2 8l10-5 M2 12l10 5 10-5 M2 16l10 5 10-5',
  shop: 'M3 10v11h18V10 M2 10l2-7h16l2 7 M2 10c0 4 5 4 5 0 0 4 5 4 5 0 0 4 5 4 5 0 0 4 5 4 5 0 M9 21v-7h6v7',
  database: 'M20 5c0 2-4 3-8 3S4 7 4 5s4-3 8-3 8 1 8 3 M4 5v14c0 2 4 3 8 3s8-1 8-3V5 M4 12c0 2 4 3 8 3s8-1 8-3',
  calendar: 'M5 4h14a2 2 0 0 1 2 2v14H3V6a2 2 0 0 1 2-2 M7 2v5 M17 2v5 M3 10h18 M7 14h2 M12 14h2 M7 18h2',
  refresh: 'M20 8a8 8 0 0 0-14-3L3 8 M3 3v5h5 M4 16a8 8 0 0 0 14 3l3-3 M21 21v-5h-5',
  copy: 'M8 8V4h12v12h-4 M4 8h12v12H4z',
  chevron: 'm9 5 7 7-7 7',
  arrow: 'M5 12h14 m-6-6 6 6-6 6',
  money: 'M3 5h18v14H3z M7 5v14 M17 5v14 M14 12a2 2 0 1 1-4 0 2 2 0 0 1 4 0',
  orders: 'M6 3h12v18l-3-2-3 2-3-2-3 2V3 M9 7h6 M9 11h6 M9 15h3',
  users: 'M16 21v-3a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v3 M13 6a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M22 21v-3a4 4 0 0 0-3-3.8 M16 2.2a4 4 0 0 1 0 7.6',
  average: 'M3 19h18 M5 15V9 M12 15V3 M19 15V6',
  info: 'M12 8h.01 M12 11v6 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  send: 'm3 3 19 9-19 9 4-9-4-9 M7 12h15',
  search: 'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  plus: 'M12 4v16 M4 12h16',
  trash: 'M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13 M10 11v5 M14 11v5',
  chat: 'M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8A8.5 8.5 0 0 1 8.7 3.9a8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5z',
  chart: 'M3 3v18h18 M7 14l5-5 4 3 5-7',
};

export function Icon({ name, size = 18 }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={icons[name] || icons.chart} /></svg>;
}

export function BrandMark() { return <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>; }

export function Chart({ option, className = '' }) {
  return <ReactECharts echarts={echarts} option={{ ...option, animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches, textStyle: { fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif', ...option.textStyle } }} className={className} notMerge lazyUpdate opts={{ renderer: 'canvas' }} />;
}

export function PanelHeading({ title, note, action }) { return <div className="panel-heading"><div><h2>{title}</h2>{note && <p>{note}</p>}</div>{action}</div>; }
