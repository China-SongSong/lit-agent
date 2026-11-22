import { cli, Strategy } from '@jackwener/opencli/registry';

function clampInt(value, fallback, minimum, maximum) {
    const parsed = Number.parseInt(value ?? '', 10);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(minimum, Math.min(maximum, parsed));
}

function requireNonEmptyQuery(value) {
    const query = String(value ?? '').trim();
    if (!query) throw new Error('Search query must not be empty');
    return query;
}

cli({
    site: 'cnki',
    name: 'search',
    access: 'read',
    description: 'CNKI 论文搜索（国内 KNS8S）',
    domain: 'kns.cnki.net',
    strategy: Strategy.COOKIE,
    args: [
        { name: 'query', positional: true, required: true, help: '搜索关键词' },
        { name: 'limit', type: 'int', default: 10, help: '返回结果数量 (max 20)' },
    ],
    columns: ['rank', 'title', 'authors', 'journal', 'date', 'url'],
    navigateBefore: false,
    func: async (page, kwargs) => {
        const limit = clampInt(kwargs.limit, 10, 1, 20);
        const query = requireNonEmptyQuery(kwargs.query);
        const searchUrl = `https://kns.cnki.net/kns8s/search?dbcode=CFLS&kw=${encodeURIComponent(query)}&korder=SU`;
        await page.goto(searchUrl);
        await page.wait(5);

        const state = await page.evaluate(`
      (() => ({
        href: document.location.href,
        verification: /\\/verify\\//i.test(document.location.href) ||
          /安全验证|拖动滑块|验证码|访问验证/.test(document.body?.innerText || ''),
      }))()
    `);
        if (state?.verification) {
            throw new Error(
                'CNKI_AUTH_REQUIRED: 站点要求完成安全验证，请在专用 OpenCLI 浏览器中处理后再重试'
            );
        }

        const data = await page.evaluate(`
      (async () => {
        const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
        const selectors = [
          '.result-table-list tbody tr',
          '#gridTable tbody tr',
          '.result-list .result-item',
          '.result-item',
        ];
        for (let i = 0; i < 60; i++) {
          if (selectors.some(selector => document.querySelector(selector))) break;
          if (/没有找到|无相关|检索结果为空/.test(document.body?.innerText || '')) break;
          await new Promise(resolve => setTimeout(resolve, 500));
        }

        let nodes = [];
        for (const selector of selectors) {
          const found = Array.from(document.querySelectorAll(selector));
          if (found.length > nodes.length) nodes = found;
        }

        const results = [];
        const seen = new Set();
        for (const node of nodes) {
          const titleEl = node.querySelector(
            'td.name a, a.fz14, .result-title a, h3 a, a[href*="kcms/detail"], a[href*="kcms2/article"]'
          );
          const title = normalize(titleEl?.textContent)
            .replace(/\\s*(免费|网络首发|优先出版)$/g, '')
            .trim();
          if (!title || seen.has(title)) continue;
          seen.add(title);

          const text = normalize(node.textContent);
          let url = titleEl?.getAttribute('href') || '';
          if (url && !url.startsWith('http')) {
            try {
              url = new URL(url, 'https://kns.cnki.net').href;
            } catch {
              url = '';
            }
          }
          if (!url || url.startsWith('javascript:')) {
            const filename =
              titleEl?.getAttribute('data-filename') ||
              node.querySelector('[data-filename]')?.getAttribute('data-filename') ||
              '';
            if (filename) {
              url = 'https://kns.cnki.net/kcms2/article/abstract?v=' +
                encodeURIComponent(filename);
            }
          }

          const authorText =
            normalize(node.querySelector('td.author, .author, .authors')?.textContent) ||
            '';
          const journalText =
            normalize(node.querySelector('td.source, .source, .journal')?.textContent) ||
            '';
          const dateText =
            normalize(node.querySelector('td.date, .date, .year')?.textContent) ||
            (text.match(/(?:19|20)\\d{2}(?:-\\d{2}(?:-\\d{2})?)?/)?.[0] || '');

          results.push({
            rank: results.length + 1,
            title,
            authors: authorText,
            journal: journalText,
            date: dateText,
            url,
          });
          if (results.length >= ${limit}) break;
        }
        return results;
      })()
    `);
        return Array.isArray(data) ? data : [];
    },
});
