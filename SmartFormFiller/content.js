/**
 * Content Script - 注入页面的核心脚本
 * 负责表单扫描、模糊匹配、自动填充
 */
(() => {
  const FILLED_CLASS = 'smart-form-filler-filled';

  /**
   * 执行自动填充
   * @returns {Promise<{filled: number, skipped: number, details: Array}>}
   */
  async function performFill() {
    // 获取当前激活的配置和设置
    const [profileResp, settingsResp] = await Promise.all([
      chrome.runtime.sendMessage({ type: 'GET_ACTIVE_PROFILE' }),
      chrome.runtime.sendMessage({ type: 'GET_SETTINGS' })
    ]);

    const profile = profileResp?.profile;
    const settings = settingsResp?.settings || {};

    if (!profile || !profile.fields || profile.fields.length === 0) {
      console.log('[SmartFormFiller] 没有激活的配置档案或字段为空');
      return { filled: 0, skipped: 0, details: [] };
    }

    const sensitivity = settings.sensitivity || 0.4;
    const highlight = settings.highlightMatched !== false;
    const detectedFields = FieldDetector.scanPage();

    let filledCount = 0;
    let skippedCount = 0;
    const details = [];

    for (const { element, contextTexts } of detectedFields) {
      let bestMatch = null;
      let bestScore = 0;
      let bestContext = '';

      // 遍历所有上下文文本，找到最佳匹配
      for (const ctxText of contextTexts) {
        // 获取所有候选匹配（按分数降序）
        const candidates = FuzzyMatcher.findAllCandidates(ctxText, profile.fields);
        if (candidates.length === 0) continue;

        const top = candidates[0];
        const second = candidates.length > 1 ? candidates[1] : null;

        // 检查 1：最低分数保底（不管灵敏度多低，分数不能低于关键字长度决定的底线）
        const floor = FuzzyMatcher.minScoreFloor(top.field.key);
        if (top.score < floor) continue;

        // 检查 2：灵敏度阈值
        if (top.score < sensitivity) continue;

        // 检查 3：显著性裕度 — 最佳匹配必须显著优于次佳匹配
        //   避免“小红书主页(仅填链接...)”同时弱匹配“小红书名字”和“小红书粉丝量”
        if (second) {
          const margin = top.score - second.score;
          const minMargin = 0.12;
          if (margin < minMargin) continue;
        }

        if (top.score > bestScore) {
          bestScore = top.score;
          bestMatch = top;
          bestContext = ctxText;
        }
      }

      if (bestMatch) {
        // 使用 native setter 触发 React/Vue 等框架的响应式更新
        setNativeValue(element, bestMatch.field.value);

        // 触发事件，确保表单验证等逻辑能正常执行
        element.dispatchEvent(new Event('input', { bubbles: true }));
        element.dispatchEvent(new Event('change', { bubbles: true }));
        element.dispatchEvent(new Event('blur', { bubbles: true }));

        // 高亮已填充字段
        if (highlight) {
          element.classList.add(FILLED_CLASS);
          setTimeout(() => element.classList.remove(FILLED_CLASS), 3000);
        }

        details.push({
          key: bestMatch.field.key,
          value: bestMatch.field.value,
          score: bestScore.toFixed(3),
          context: bestContext
        });

        console.log(
          `[SmartFormFiller] ✅ "${bestMatch.field.key}" → "${bestMatch.field.value}" (分数: ${bestScore.toFixed(3)}, 上下文: "${bestContext}")`
        );
        filledCount++;
      }
    }

    return { filled: filledCount, skipped: skippedCount, details };
  }

  /**
   * 使用原生 setter 设置值（兼容 React/Vue 框架）
   */
  function setNativeValue(element, value) {
    const proto = element.tagName === 'TEXTAREA'
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;

    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) {
      setter.call(element, value);
    } else {
      element.value = value;
    }
  }

  /**
   * 扫描页面字段（调试用）
   */
  function scanFields() {
    const detected = FieldDetector.scanPage();
    return detected.map(({ element, contextTexts }) => ({
      tag: element.tagName,
      type: element.type || '',
      name: element.name || '',
      id: element.id || '',
      placeholder: element.placeholder || '',
      currentValue: element.value || '',
      contextTexts
    }));
  }

  // 监听来自 popup 的消息
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'TRIGGER_FILL') {
      performFill().then(result => {
        // 通知 background 更新角标
        chrome.runtime.sendMessage({
          type: 'FILL_COMPLETE',
          count: result.filled
        }).catch(() => {});
        sendResponse(result);
      }).catch(err => {
        sendResponse({ filled: 0, skipped: 0, error: err.message });
      });
      return true; // 异步响应
    }

    if (message.type === 'SCAN_FIELDS') {
      const fields = scanFields();
      sendResponse({ fields });
      return true;
    }
  });

  console.log('[SmartFormFiller] 内容脚本已加载 ✅');
})();
