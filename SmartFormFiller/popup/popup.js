/**
 * Popup 逻辑
 * 负责 Tab 切换、配置管理、一键填充、扫描调试
 */
document.addEventListener('DOMContentLoaded', () => {
  // ==================== Tab 切换 ====================
  const tabs = document.querySelectorAll('.tab-btn');
  const contents = document.querySelectorAll('.tab-content');

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      contents.forEach(c => c.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById(`tab-${tab.dataset.tab}`).classList.add('active');
    });
  });

  // ==================== 状态 ====================
  let currentEditProfileId = null;
  let currentEditFields = [];

  // ==================== 工具函数 ====================

  /** HTML 转义，防止 XSS */
  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  /** 向 background 发送消息 */
  function sendMessage(msg) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage(msg, (response) => {
        resolve(response);
      });
    });
  }

  /** 获取当前活跃标签页 */
  async function getActiveTab() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    return tab;
  }

  // ==================== 一键填充 Tab ====================

  /** 刷新配置下拉框 */
  async function refreshProfileSelect() {
    const { profiles, activeId } = await sendMessage({ type: 'GET_ALL_PROFILES' });
    const select = document.getElementById('profileSelect');
    select.innerHTML = '';

    const keys = Object.keys(profiles || {});
    if (keys.length === 0) {
      select.innerHTML = '<option value="">暂无配置</option>';
      document.getElementById('fieldCount').innerHTML = '已配置 <strong>0</strong> 个字段';
      document.getElementById('fieldPreview').innerHTML = '';
      return;
    }

    keys.forEach(id => {
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = profiles[id].name;
      if (id === activeId) opt.selected = true;
      select.appendChild(opt);
    });

    const active = profiles[activeId];
    if (active) {
      document.getElementById('fieldCount').innerHTML =
        `已配置 <strong>${active.fields.length}</strong> 个字段`;

      // 字段预览
      const preview = document.getElementById('fieldPreview');
      preview.innerHTML = active.fields.map(f =>
        `<span>${escapeHtml(f.key)} → ${escapeHtml(f.value)}</span>`
      ).join('');
    }
  }

  /** 切换配置档案 */
  document.getElementById('profileSelect').addEventListener('change', async (e) => {
    await sendMessage({ type: 'SET_ACTIVE_PROFILE', id: e.target.value });
    await refreshProfileSelect();
  });

  /** 一键填充按钮 */
  document.getElementById('btnFill').addEventListener('click', async () => {
    const tab = await getActiveTab();
    if (!tab) return;

    const resultBox = document.getElementById('fillResult');
    document.getElementById('scanResult').style.display = 'none';

    try {
      const response = await chrome.tabs.sendMessage(tab.id, { type: 'TRIGGER_FILL' });

      if (response.error) {
        resultBox.className = 'result-box error';
        resultBox.textContent = '❌ ' + response.error;
      } else {
        resultBox.className = 'result-box';
        let html = `✅ 填充完成！成功填充 <strong>${response.filled}</strong> 个字段`;

        if (response.details && response.details.length > 0) {
          html += '<br><br><strong>填充详情：</strong><br>';
          response.details.forEach(d => {
            html += `• "${escapeHtml(d.key)}" → "${escapeHtml(d.value)}" <span style="color:#999">(分数:${escapeHtml(d.score)}, 上下文:"${escapeHtml(d.context)}")</span><br>`;
          });
        }

        resultBox.innerHTML = html;
      }
      resultBox.style.display = 'block';
    } catch (err) {
      resultBox.style.display = 'block';
      resultBox.className = 'result-box error';
      resultBox.textContent = '❌ 无法连接页面，请刷新页面后重试。错误: ' + err.message;
    }
  });

  /** 扫描页面字段按钮 */
  document.getElementById('btnScan').addEventListener('click', async () => {
    const tab = await getActiveTab();
    if (!tab) return;

    const resultBox = document.getElementById('scanResult');
    document.getElementById('fillResult').style.display = 'none';

    try {
      const response = await chrome.tabs.sendMessage(tab.id, { type: 'SCAN_FIELDS' });
      resultBox.style.display = 'block';
      resultBox.className = 'result-box';

      if (!response.fields || response.fields.length === 0) {
        resultBox.textContent = '🔍 未检测到可填充的表单字段';
      } else {
        let html = `<strong>🔍 检测到 ${response.fields.length} 个表单字段：</strong><br><br>`;
        response.fields.forEach((f, i) => {
          html += `<strong>[${i + 1}]</strong> ${escapeHtml(f.tag)} type="${escapeHtml(f.type)}" name="${escapeHtml(f.name) || '-'}" placeholder="${escapeHtml(f.placeholder) || '-'}"<br>`;
          if (f.contextTexts.length > 0) {
            html += `   上下文: ${escapeHtml(f.contextTexts.slice(0, 5).join(' | '))}<br>`;
          }
          html += '<br>';
        });
        resultBox.innerHTML = html;
      }
    } catch (err) {
      resultBox.style.display = 'block';
      resultBox.className = 'result-box error';
      resultBox.textContent = '❌ 无法连接页面: ' + err.message;
    }
  });

  // ==================== 信息管理 Tab ====================

  /** 刷新配置档案列表 */
  async function refreshProfileList() {
    const { profiles, activeId } = await sendMessage({ type: 'GET_ALL_PROFILES' });
    const list = document.getElementById('profileList');
    list.innerHTML = '';

    Object.values(profiles || {}).forEach(profile => {
      const card = document.createElement('div');
      card.className = `profile-card ${profile.id === activeId ? 'active' : ''}`;
      card.innerHTML = `
        <div>
          <div class="profile-name">${escapeHtml(profile.name)}</div>
          <div class="profile-meta">${profile.fields.length} 个字段</div>
        </div>
        ${profile.id === activeId ? '<span class="profile-badge">使用中</span>' : ''}
      `;
      card.addEventListener('click', () => {
        currentEditProfileId = profile.id;
        currentEditFields = JSON.parse(JSON.stringify(profile.fields));
        openFieldEditor(profile.name);
      });
      list.appendChild(card);
    });
  }

  /** 打开字段编辑器 */
  function openFieldEditor(profileName) {
    const editor = document.getElementById('fieldEditor');
    editor.style.display = 'block';
    document.getElementById('editorTitle').textContent = `编辑: ${profileName}`;
    renderFieldList();
  }

  /** 渲染字段列表 */
  function renderFieldList() {
    const list = document.getElementById('fieldList');
    list.innerHTML = '';

    if (currentEditFields.length === 0) {
      list.innerHTML = '<p style="text-align:center;color:#999;padding:10px;">暂无字段，请添加</p>';
      return;
    }

    currentEditFields.forEach((field, index) => {
      const item = document.createElement('div');
      item.className = 'field-item';
      item.innerHTML = `
        <span class="field-key">${escapeHtml(field.key)}</span>
        <span class="field-arrow">→</span>
        <span class="field-value">${escapeHtml(field.value)}</span>
        <span class="field-delete" data-index="${index}" title="删除">✕</span>
      `;
      list.appendChild(item);
    });

    // 绑定删除事件
    list.querySelectorAll('.field-delete').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const idx = parseInt(e.target.dataset.index);
        currentEditFields.splice(idx, 1);
        renderFieldList();
      });
    });
  }

  /** 新建配置档案 */
  document.getElementById('btnAddProfile').addEventListener('click', async () => {
    const name = prompt('请输入配置名称：');
    if (!name || !name.trim()) return;

    const id = 'profile_' + Date.now();
    const profile = { id, name: name.trim(), fields: [] };

    await sendMessage({ type: 'SAVE_PROFILE', profile });
    await refreshProfileList();
    await refreshProfileSelect();

    currentEditProfileId = id;
    currentEditFields = [];
    openFieldEditor(name.trim());
  });

  /** 添加字段 */
  document.getElementById('btnAddField').addEventListener('click', () => {
    const keyInput = document.getElementById('newFieldKey');
    const valueInput = document.getElementById('newFieldValue');
    const key = keyInput.value.trim();
    const value = valueInput.value.trim();

    if (!key || !value) {
      alert('请填写匹配关键字和填充值');
      return;
    }

    currentEditFields.push({ key, value });
    renderFieldList();
    keyInput.value = '';
    valueInput.value = '';
    keyInput.focus();
  });

  /** 保存配置 */
  document.getElementById('btnSaveProfile').addEventListener('click', async () => {
    if (!currentEditProfileId) return;

    // 获取当前 profile 名称
    const { profiles } = await sendMessage({ type: 'GET_ALL_PROFILES' });
    const existing = profiles[currentEditProfileId];
    const profileName = existing ? existing.name : '未命名';

    const profile = {
      id: currentEditProfileId,
      name: profileName,
      fields: currentEditFields
    };

    await sendMessage({ type: 'SAVE_PROFILE', profile });
    await refreshProfileList();
    await refreshProfileSelect();
    alert('保存成功！');
  });

  /** 删除配置 */
  document.getElementById('btnDeleteProfile').addEventListener('click', async () => {
    if (!currentEditProfileId) return;
    if (!confirm('确定删除此配置档案？此操作不可恢复。')) return;

    await sendMessage({ type: 'DELETE_PROFILE', id: currentEditProfileId });
    currentEditProfileId = null;
    currentEditFields = [];
    document.getElementById('fieldEditor').style.display = 'none';
    await refreshProfileList();
    await refreshProfileSelect();
  });

  // Enter 键快捷添加字段
  document.getElementById('newFieldValue').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      document.getElementById('btnAddField').click();
    }
  });

  // ==================== 设置 Tab ====================

  /** 初始化设置 */
  async function initSettings() {
    const { settings } = await sendMessage({ type: 'GET_SETTINGS' });
    const s = settings || {};
    document.getElementById('sensitivitySlider').value = s.sensitivity || 0.4;
    document.getElementById('sensitivityValue').textContent = s.sensitivity || 0.4;
    document.getElementById('chkHighlight').checked = s.highlightMatched !== false;
    document.getElementById('chkBadge').checked = s.showBadge !== false;
  }

  /** 灵敏度滑块 */
  document.getElementById('sensitivitySlider').addEventListener('input', (e) => {
    document.getElementById('sensitivityValue').textContent = e.target.value;
  });

  /** 保存设置 */
  document.getElementById('btnSaveSettings').addEventListener('click', async () => {
    const settings = {
      sensitivity: parseFloat(document.getElementById('sensitivitySlider').value),
      highlightMatched: document.getElementById('chkHighlight').checked,
      showBadge: document.getElementById('chkBadge').checked
    };

    await sendMessage({ type: 'SAVE_SETTINGS', settings });

    const resultBox = document.getElementById('settingsResult');
    resultBox.style.display = 'block';
    resultBox.className = 'result-box';
    resultBox.textContent = '✅ 设置已保存！';
    setTimeout(() => { resultBox.style.display = 'none'; }, 2000);
  });

  // ==================== 初始化 ====================
  refreshProfileSelect();
  refreshProfileList();
  initSettings();
});
