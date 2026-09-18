/**
 * Content Script - 注入页面的核心脚本
 * 负责表单扫描、模糊匹配、自动填充
 */
(() => {
  const FILLED_CLASS = 'smart-form-filler-filled';

  /**
   * 执行自动填充
   * @returns {Promise<{filled: number, details: Array}>}
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
      return { filled: 0, details: [] };
    }

    const sensitivity = settings.sensitivity || 0.4;
    const highlight = settings.highlightMatched !== false;
    const detectedFields = FieldDetector.scanPage();

    let filledCount = 0;
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
        // 设置值并触发事件（setNativeValue 已内置 React/Vue 兼容处理）
        setNativeValue(element, bestMatch.field.value);

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

    return { filled: filledCount, details };
  }

  /**
   * 使用原生 setter 设置值（兼容 React/Vue 框架）
   * 对于 React 受控组件，需要通过模拟真实键盘输入事件来触发 onChange
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

    // React 受控组件兼容：按真实键盘事件顺序派发
    // 顺序：keydown → input → keyup → change
    element.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Unidentified' }));
    element.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      cancelable: true,
      inputType: 'insertText',
      data: value
    }));
    element.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Unidentified' }));
    element.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
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
        sendResponse({ filled: 0, error: err.message });
      });
      return true; // 异步响应
    }

    if (message.type === 'SCAN_FIELDS') {
      const fields = scanFields();
      sendResponse({ fields });
      return true;
    }
  });

  // ==================== 手动选择面板 (Picker) ====================

  let activePicker = null;       // 当前显示的面板 DOM 元素
  let activeTargetInput = null;  // 当前目标输入框
  let pickerProfile = null;      // 缓存的配置档案

  /** 复用 FieldDetector 的选择器 */
  const PICKER_INPUT_SELECTORS = FieldDetector.INPUT_SELECTORS;

  const SFF_BTN_CLASS = 'sff-float-btn';

  /**
   * 查找输入框关联的标签元素（标题/名称）
   * 向上遍历祖先链，查找常见的标签容器
   */
  function findLabelForInput(input) {
    let current = input.parentElement;
    let level = 0;
    while (current && level < 8) {
      // 查找已知的标签容器选择器
      const labelEl = current.querySelector(
        '[class*="question-title"], [class*="field-label"], [class*="form-label"], ' +
        '[class*="item-label"], [class*="label-text"], [class*="field-title"], ' +
        '[class*="form-title"], [class*="item-title"], [class*="question-name"]'
      );
      if (labelEl) return labelEl;

      // 查找 <label> 元素
      if (current.tagName === 'LABEL') return current;

      current = current.parentElement;
      level++;
    }
    return null;
  }

  /**
   * 扫描页面并为每个表单输入框注入按钮（放在标签/名称后面）
   */
  function injectFloatButtons() {
    // 先清除旧按钮
    document.querySelectorAll('.' + SFF_BTN_CLASS).forEach(btn => btn.remove());

    const inputs = document.querySelectorAll(PICKER_INPUT_SELECTORS);
    inputs.forEach(input => {
      if (input.offsetParent === null && input.type !== 'hidden') return;
      if (input.disabled || input.readOnly) return;

      // 查找关联的标签元素
      const labelEl = findLabelForInput(input);
      // 插入位置：标签元素后面，如果没有标签则放在输入框后面
      const insertTarget = labelEl || input;
      const insertContainer = insertTarget.parentElement;
      if (!insertContainer) return;

      // 避免重复注入
      if (insertContainer.querySelector('.' + SFF_BTN_CLASS)) return;

      const btn = document.createElement('div');
      btn.className = SFF_BTN_CLASS;
      btn.innerHTML = '<span class="sff-btn-icon">📝</span><span class="sff-btn-text">手动选择预设字段</span>';

      // 插入到标签名称后面
      insertContainer.insertBefore(btn, insertTarget.nextSibling);

      // 点击按钮 → 显示面板
      btn.addEventListener('mousedown', (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        showPicker(input, btn);
      });
    });
  }

  /**
   * 在按钮旁显示手动选择面板
   */
  async function showPicker(targetInput, anchorBtn) {
    // 如果点击的是同一个按钮，切换关闭
    if (activePicker && activeTargetInput === targetInput) {
      closePicker();
      return;
    }

    // 关闭已有面板
    closePicker();

    // 获取当前配置档案
    const resp = await chrome.runtime.sendMessage({ type: 'GET_ACTIVE_PROFILE' });
    const profile = resp?.profile;
    if (!profile || !profile.fields || profile.fields.length === 0) return;

    pickerProfile = profile;
    activeTargetInput = targetInput;

    // 创建面板
    const panel = document.createElement('div');
    panel.className = 'sff-picker-panel';

    // 头部
    const header = document.createElement('div');
    header.className = 'sff-picker-header';
    header.innerHTML = `
      <span class="sff-title">📝 ${escapeHtmlForPicker(profile.name)}</span>
      <button class="sff-close" title="关闭">✕</button>
    `;
    header.querySelector('.sff-close').addEventListener('click', (e) => {
      e.stopPropagation();
      closePicker();
    });

    // 搜索框
    const searchWrap = document.createElement('div');
    searchWrap.className = 'sff-picker-search';
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.placeholder = '搜索字段...';
    searchInput.addEventListener('input', () => renderPickerList(listEl, searchInput.value));
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closePicker();
    });
    searchInput.addEventListener('mousedown', (e) => e.stopPropagation());
    searchWrap.appendChild(searchInput);

    // 字段列表
    const listEl = document.createElement('div');
    listEl.className = 'sff-picker-list';
    renderPickerList(listEl, '');

    panel.appendChild(header);
    panel.appendChild(searchWrap);
    panel.appendChild(listEl);

    // 先绑定事件（防止添加到 DOM 后瞬间的点击穿透）
    panel.addEventListener('mousedown', (e) => e.stopPropagation());
    panel.addEventListener('click', (e) => e.stopPropagation());

    // 计算位置（基于按钮位置）并添加到 DOM
    positionPanelByAnchor(panel, anchorBtn);
    document.body.appendChild(panel);
    activePicker = panel;

    // 聚焦搜索框
    setTimeout(() => searchInput.focus(), 50);
  }

  /**
   * 渲染字段列表（支持搜索过滤）
   */
  function renderPickerList(listEl, query) {
    listEl.innerHTML = '';
    const q = query.trim().toLowerCase();

    const filtered = pickerProfile.fields.filter(f => {
      if (!q) return true;
      return f.key.toLowerCase().includes(q) || f.value.toLowerCase().includes(q);
    });

    if (filtered.length === 0) {
      listEl.innerHTML = '<div class="sff-picker-empty">无匹配字段</div>';
      return;
    }

    filtered.forEach(field => {
      const item = document.createElement('div');
      item.className = 'sff-picker-item';
      item.innerHTML = `
        <span class="sff-key">${escapeHtmlForPicker(field.key)}</span>
        <span class="sff-arrow">→</span>
        <span class="sff-value">${escapeHtmlForPicker(field.value)}</span>
      `;
      item.addEventListener('mousedown', (e) => {
        e.preventDefault(); // 防止失焦
        e.stopPropagation();
      });
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        if (activeTargetInput) {
          const targetEl = activeTargetInput; // 保存引用，防止 closePicker 后置 null
          targetEl.focus(); // 聚焦后再设值，确保 React 受控组件响应
          setNativeValue(targetEl, field.value);
          targetEl.classList.add('smart-form-filler-filled');
          setTimeout(() => targetEl.classList.remove('smart-form-filler-filled'), 3000);
        }
        closePicker();
      });
      listEl.appendChild(item);
    });
  }

  /**
   * 基于锚点按钮计算面板位置
   */
  function positionPanelByAnchor(panel, anchor) {
    const rect = anchor.getBoundingClientRect();
    const panelWidth = 300;
    const panelMaxHeight = 360;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let left = rect.left;
    if (left + panelWidth > vw - 10) left = vw - panelWidth - 10;
    if (left < 10) left = 10;

    const spaceBelow = vh - rect.bottom;
    const spaceAbove = rect.top;
    let top;

    if (spaceBelow >= panelMaxHeight + 10 || spaceBelow >= spaceAbove) {
      top = rect.bottom + 4;
    } else {
      top = rect.top - panelMaxHeight - 4;
    }

    panel.style.left = left + 'px';
    panel.style.top = top + 'px';
  }

  /**
   * 关闭面板
   */
  function closePicker() {
    if (activePicker) {
      activePicker.remove();
      activePicker = null;
    }
    activeTargetInput = null;
    pickerProfile = null;
  }

  /**
   * 面板内用的 HTML 转义
   */
  function escapeHtmlForPicker(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ==================== 事件监听 ====================

  /**
   * 点击面板外部时关闭
   */
  document.addEventListener('mousedown', (e) => {
    if (!activePicker) return;
    const target = e.target;
    if (activePicker.contains(target)) return;
    if (target && target.classList && target.classList.contains(SFF_BTN_CLASS)) return;
    closePicker();
  }, true);

  /**
   * Escape 键关闭面板
   */
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && activePicker) {
      closePicker();
    }
  }, true);

  /**
   * 页面加载后注入按钮（输入框后面）
   * 延迟执行以确保页面动态渲染的表单已完成
   */
  function scheduleInject() {
    injectFloatButtons();
    // 2 秒后再扫一次（捕获延迟渲染的表单）
    setTimeout(injectFloatButtons, 2000);
  }

  // 初始注入
  scheduleInject();

  // 监听 DOM 变化（动态加载的表单）
  const observer = new MutationObserver(() => {
    clearTimeout(observer._timer);
    observer._timer = setTimeout(injectFloatButtons, 500);
  });
  observer.observe(document.body, { childList: true, subtree: true });

  console.log('[SmartFormFiller] 内容脚本已加载 ✅');
})();
