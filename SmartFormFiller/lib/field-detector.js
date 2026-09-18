/**
 * FieldDetector - DOM 表单字段检测器
 * 从多个维度提取表单元素的上下文信息，用于模糊匹配
 */
const FieldDetector = (() => {

  /** 支持的表单元素选择器 */
  const INPUT_SELECTORS = [
    'input[type="text"]',
    'input[type="tel"]',
    'input[type="email"]',
    'input[type="url"]',
    'input[type="number"]',
    'input[type="search"]',
    'input:not([type])',
    'textarea'
  ].join(', ');

  /**
   * 常见的表单标签容器选择器（覆盖腾讯文档、飞书、金数据等）
   */
  const LABEL_CONTAINER_SELECTORS = [
    '[class*="question-title"]',
    '[class*="field-label"]',
    '[class*="form-label"]',
    '[class*="item-label"]',
    '[class*="input-label"]',
    '[class*="label-text"]',
    '[class*="field-title"]',
    '[class*="form-title"]',
    '[class*="item-title"]',
    '[class*="question-name"]'
  ].join(', ');

  /**
   * 提取表单元素的所有标签/上下文文本
   * 从多个维度：label、placeholder、name、id、aria-label、祖先兄弟标签、邻近文本
   */
  function getLabelText(element) {
    const texts = [];

    // 1. 关联的 <label for="...">
    if (element.id) {
      try {
        const label = document.querySelector(`label[for="${CSS.escape(element.id)}"]`);
        if (label) texts.push(label.textContent);
      } catch (e) { /* ignore */ }
    }

    // 2. 父级 <label>
    const parentLabel = element.closest('label');
    if (parentLabel) {
      const clone = parentLabel.cloneNode(true);
      const inputs = clone.querySelectorAll('input, textarea, select');
      inputs.forEach(inp => inp.remove());
      texts.push(clone.textContent);
    }

    // 3. aria-label
    const ariaLabel = element.getAttribute('aria-label');
    if (ariaLabel) texts.push(ariaLabel);

    // 4. placeholder
    const placeholder = element.getAttribute('placeholder');
    if (placeholder) texts.push(placeholder);

    // 5. name 属性
    const name = element.getAttribute('name');
    if (name) texts.push(name.replace(/[_\-]/g, ' '));

    // 6. id 属性
    const id = element.id;
    if (id) texts.push(id.replace(/[_\-]/g, ' '));

    // 7. 沿祖先链向上查找标签容器（核心增强：支持腾讯文档等自定义表单）
    let current = element.parentElement;
    let level = 0;
    while (current && level < 8) {
      // 7a. 查找已知的标签容器选择器
      try {
        const labelContainers = current.querySelectorAll(LABEL_CONTAINER_SELECTORS);
        labelContainers.forEach(lc => {
          const t = lc.textContent?.trim();
          if (t && t.length < 80) texts.push(t);
        });
      } catch (e) { /* ignore */ }

      // 7b. 查找前置兄弟元素（在当前层级的同辈中查找）
      const prevSibling = current.previousElementSibling;
      if (prevSibling) {
        const clone = prevSibling.cloneNode(true);
        const inputs = clone.querySelectorAll('input, textarea, select');
        inputs.forEach(inp => inp.remove());
        const t = (clone.textContent || '').trim();
        if (t && t.length < 80 && t.length > 0) texts.push(t);
      }

      // 7c. 在父容器中，排除当前 input 分支后提取剩余文本
      //     仅当父容器的子节点数较少时执行（避免提取过多无关文本）
      if (current.children.length <= 6) {
        const clone = current.cloneNode(true);
        // 移除当前 input 元素及其所有祖先分支
        const inputParent = clone.querySelector(
          element.tagName.toLowerCase() +
          (element.id ? `#${CSS.escape(element.id)}` : '') +
          (element.getAttribute('placeholder') ? `[placeholder="${element.getAttribute('placeholder')}"]` : '')
        );
        if (inputParent) inputParent.remove();
        else {
          // 如果通过选择器找不到，直接移除所有 input/textarea
          clone.querySelectorAll('input, textarea, select').forEach(inp => inp.remove());
        }
        const remainText = (clone.textContent || '').trim();
        if (remainText && remainText.length < 60 && remainText.length > 0) {
          texts.push(remainText);
        }
      }

      current = current.parentElement;
      level++;
    }

    return texts.filter(t => t && t.trim().length > 0).map(t => t.trim());
  }

  /**
   * 获取元素附近的所有上下文文本（包含距离过滤）
   * 增强：向上查找更多层级的祖先容器中的文本
   */
  function getAllContextTexts(element) {
    const texts = getLabelText(element);

    // 向上查找多层级容器，扫描附近文本节点
    const containers = [];
    let el = element.parentElement;
    for (let i = 0; i < 5 && el; i++) {
      containers.push(el);
      el = el.parentElement;
    }

    const nearby = [];
    containers.forEach(container => {
      const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
      let node;
      while ((node = walker.nextNode())) {
        const text = node.textContent.trim();
        if (text && text.length < 50 && text.length > 1) {
          const parent = node.parentElement;
          if (parent && parent !== element) {
            try {
              const rect = parent.getBoundingClientRect();
              const elRect = element.getBoundingClientRect();
              const distance = Math.abs(rect.top - elRect.top) + Math.abs(rect.left - elRect.left);
              if (distance < 200) {
                nearby.push(text);
              }
            } catch (e) { /* ignore */ }
          }
        }
      }
    });

    return [...new Set([...texts, ...nearby])];
  }

  /**
   * 扫描页面中所有可填充的表单元素
   * @returns {Array<{element: HTMLElement, contextTexts: string[]}>}
   */
  function scanPage() {
    const elements = document.querySelectorAll(INPUT_SELECTORS);
    const fields = [];

    elements.forEach(el => {
      // 跳过隐藏元素
      if (el.offsetParent === null && el.type !== 'hidden') return;
      // 跳过 disabled / readonly
      if (el.disabled || el.readOnly) return;

      const contextTexts = getAllContextTexts(el);
      fields.push({ element: el, contextTexts });
    });

    return fields;
  }

  return { scanPage, getAllContextTexts, getLabelText, INPUT_SELECTORS };
})();

if (typeof window !== 'undefined') {
  window.FieldDetector = FieldDetector;
}
