// 抖音主页抓取脚本（在已登录的浏览器页面上下文中执行）
//
// 用法（Playwright 等价流程）：
//   1. 打开 https://www.douyin.com/user/<sec_uid>，等页面渲染完成
//   2. 先滚动加载全量：
//        const sc = document.querySelector('div.parent-route-container');
//        sc.scrollTo(0, sc.scrollHeight);   // 重复直到 ul[data-e2e=scroll-list] li 数量稳定
//   3. 注入下面的 initScript 后 reload，再重复滚动，最后调用 window.__dumpDouyinCorpus()
//   4. 结果 JSON 交给 scripts/build_manifest.py --raw
//
// 关键事实：
//   - 滚动容器是 div.parent-route-container，不是 window
//   - 未登录只放行约 21 条，必须登录
//   - 一次主页滚动即可拿到全部作品字段与 play_addr 直链

(() => {
  if (window.__douyinCorpusHooked) return;
  window.__douyinCorpusHooked = true;
  window.__awemeCap = [];

  const keep = (u) => u && (u.indexOf('/aweme/post') >= 0 || u.indexOf('/aweme/detail') >= 0);

  const stash = (url, text) => {
    try {
      const json = JSON.parse(text);
      if (json && Array.isArray(json.aweme_list)) {
        window.__awemeCap.push({ url, list: json.aweme_list });
      } else if (json && json.aweme_detail) {
        window.__awemeCap.push({ url, list: [json.aweme_detail] });
      }
    } catch (e) {
      /* 忽略非 JSON 响应 */
    }
  };

  const originalFetch = window.fetch;
  window.fetch = function () {
    const args = arguments;
    const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
    return originalFetch.apply(this, args).then((res) => {
      if (keep(url)) {
        try {
          res.clone().text().then((t) => stash(url, t)).catch(() => {});
        } catch (e) {}
      }
      return res;
    });
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__url = url;
    return originalOpen.apply(this, arguments);
  };
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    if (keep(this.__url)) {
      this.addEventListener('load', () => {
        try {
          stash(this.__url, this.responseText);
        } catch (e) {}
      });
    }
    return originalSend.apply(this, arguments);
  };

  // 把捕获结果整理成 build_manifest.py 需要的结构
  window.__dumpDouyinCorpus = () => {
    const all = (window.__awemeCap || []).flatMap((b) => b.list || []);
    const seen = new Set();
    const items = [];
    for (const a of all) {
      if (!a || !a.aweme_id || seen.has(a.aweme_id)) continue;
      seen.add(a.aweme_id);
      const st = a.statistics || {};
      const play = a.video && a.video.play_addr && a.video.play_addr.url_list;
      items.push({
        id: a.aweme_id,
        desc: a.desc || '',
        createTime: a.create_time || null,
        duration: a.video ? a.video.duration : null,
        isTop: a.is_top ? 1 : 0,
        digg: st.digg_count === undefined ? 0 : st.digg_count,
        comment: st.comment_count === undefined ? 0 : st.comment_count,
        collect: st.collect_count === undefined ? 0 : st.collect_count,
        share: st.share_count === undefined ? 0 : st.share_count,
        imageCount: Array.isArray(a.images) ? a.images.length : 0,
        playUrl: play && play.length ? play[0] : '',
        shareUrl: a.share_url || '',
      });
    }
    const author = (all[0] && all[0].author) || {};
    const tab = document.querySelector('[data-e2e=user-tab-count]');
    return JSON.stringify({
      profileUrl: location.href,
      nickname: author.nickname || document.title.replace('的抖音', ''),
      secUid: author.sec_uid || '',
      tabTotal: tab ? tab.innerText : null,
      capturedAt: new Date().toISOString(),
      count: items.length,
      items,
    });
  };
})();
