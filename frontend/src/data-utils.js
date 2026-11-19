export const money = (value, currency = 'BRL', locale = 'zh-CN') => new Intl.NumberFormat(locale, {
  style: 'currency', currency, maximumFractionDigits: 2,
}).format(Number(value ?? 0));

export const integer = (value) => new Intl.NumberFormat('zh-CN', {
  maximumFractionDigits: 2,
}).format(Number(value ?? 0));

export function partialLastMonth(date) {
  const end = new Date(`${date}T00:00:00`);
  return end.getDate() < new Date(end.getFullYear(), end.getMonth() + 1, 0).getDate();
}

// 仅提交接口接受的角色与文本，限制历史长度。
export function chatHistory(messages) {
  return messages.slice(-12).map((message) => ({
    role: message.role === 'assistant' ? 'assistant' : 'user',
    content: message.text.slice(0, 2000),
  }));
}

export function trendRows(rows, year = 'all', metric = 'gmv') {
  return rows.filter((row) => year === 'all' || row.year_month.startsWith(`${year}-`)).map((row) => ({
    ...row,
    value: metric === 'orders' ? Number(row.monthly_orders) : metric === 'aov'
      ? Number(row.monthly_orders) > 0 ? Number(row.monthly_gmv) / Number(row.monthly_orders) : null
      : Number(row.monthly_gmv),
  }));
}

export function searchRanking(rows, query, fields) {
  const term = query.trim().toLowerCase();
  return rows.filter((row) => fields.some((field) => String(row[field] ?? '').toLowerCase().includes(term)));
}
