import assert from 'node:assert/strict';
import test from 'node:test';
import { money, integer, partialLastMonth, chatHistory, trendRows, searchRanking } from './data-utils.js';

test('金额保留两位小数，普通数值保留小数信息', () => {
  assert.match(money(160.23), /160\.23/);
  assert.match(money(160.23, 'CNY', 'zh-CN'), /¥/);
  assert.equal(integer(98207), '98,207');
  assert.equal(integer(1.25), '1.25');
});

test('趋势年份仅过滤图表行，客单价按月 GMV/订单计算，零订单为空值', () => {
  const rows = [{ year_month: '2017-01', monthly_gmv: 100, monthly_orders: 2 }, { year_month: '2018-01', monthly_gmv: 0, monthly_orders: 0 }];
  assert.deepEqual(trendRows(rows, '2017', 'aov').map((row) => row.value), [50]);
  assert.equal(trendRows(rows, '2018', 'aov')[0].value, null);
  assert.deepEqual(trendRows(rows, 'all', 'orders').map((row) => row.value), [2, 0]);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].value, undefined);
});

test('排行检索支持空关键词、大小写、空值及多字段，不改变排名', () => {
  const rows = [{ rank: 1, city: 'Sao Paulo', state: 'SP' }, { rank: 2, city: null, state: 'RJ' }];
  assert.equal(searchRanking(rows, '', ['city', 'state']).length, 2);
  assert.deepEqual(searchRanking(rows, ' sp ', ['city', 'state']), [rows[0]]);
  assert.deepEqual(searchRanking(rows, 'paulo', ['city', 'state']), [rows[0]]);
  assert.deepEqual(searchRanking(rows, 'xyz', ['city', 'state']), []);
});

test('末月完整性包含平年、闰年和跨年边界', () => {
  assert.equal(partialLastMonth('2018-09-03'), true);
  for (const date of ['2017-02-28', '2016-02-29', '2017-12-31', '2018-09-30']) {
    assert.equal(partialLastMonth(date), false);
  }
  assert.equal(partialLastMonth('2016-02-28'), true);
});

test('问数历史不带图表、SQL，仅保留最近十二条和受限文本', () => {
  const messages = Array.from({ length: 20 }, (_, index) => ({ role: index % 2 ? 'assistant' : 'user', text: `${index}`.padEnd(2500, 'a'), data: { sql: 'SELECT 1' } }));
  const history = chatHistory(messages);
  assert.equal(history.length, 12);
  assert.equal(history[0].content[0], '8');
  assert.equal(history.at(-1).role, 'assistant');
  assert.ok(history.every((message) => message.content.length === 2000));
  assert.deepEqual(Object.keys(history[0]), ['role', 'content']);
});
