/**
 * FormStorage - 存储管理模块
 * 基于 chrome.storage.local 的配置档案 CRUD
 */
const FormStorage = (() => {

  const DEFAULT_SETTINGS = {
    sensitivity: 0.4,
    autoFill: false,
    highlightMatched: true,
    showBadge: true
  };

  const DEFAULT_PROFILE = {
    id: 'default',
    name: '默认配置',
    fields: [
      { key: '小红书名字', value: '张三' },
      { key: '小红书粉丝量', value: '666' },
      { key: '小红书简介', value: '热爱生活的小仙女' },
      { key: '微信昵称', value: '张三丰' },
      { key: '手机号', value: '13800138000' },
      { key: '邮箱', value: 'zhangsan@example.com' }
    ]
  };

  /**
   * 获取当前激活的配置档案
   */
  function getActiveProfile() {
    return new Promise((resolve) => {
      chrome.storage.local.get(['activeProfileId', 'profiles'], (result) => {
        const profiles = result.profiles || {};
        const activeId = result.activeProfileId;
        const profile = profiles[activeId] || profiles[Object.keys(profiles)[0]] || null;
        resolve(profile);
      });
    });
  }

  /**
   * 获取设置项
   */
  function getSettings() {
    return new Promise((resolve) => {
      chrome.storage.local.get(['settings'], (result) => {
        resolve({ ...DEFAULT_SETTINGS, ...result.settings });
      });
    });
  }

  /**
   * 保存配置档案
   */
  function saveProfile(profile) {
    return new Promise((resolve) => {
      chrome.storage.local.get(['profiles'], (result) => {
        const profiles = result.profiles || {};
        profiles[profile.id] = profile;
        chrome.storage.local.set({ profiles }, resolve);
      });
    });
  }

  /**
   * 删除配置档案
   */
  function deleteProfile(id) {
    return new Promise((resolve) => {
      chrome.storage.local.get(['profiles', 'activeProfileId'], (result) => {
        const profiles = result.profiles || {};
        delete profiles[id];
        const updates = { profiles };
        if (result.activeProfileId === id) {
          const keys = Object.keys(profiles);
          updates.activeProfileId = keys.length > 0 ? keys[0] : null;
        }
        chrome.storage.local.set(updates, resolve);
      });
    });
  }

  /**
   * 获取所有配置档案
   */
  function getAllProfiles() {
    return new Promise((resolve) => {
      chrome.storage.local.get(['profiles', 'activeProfileId'], (result) => {
        resolve({
          profiles: result.profiles || {},
          activeId: result.activeProfileId || null
        });
      });
    });
  }

  /**
   * 设置当前激活的配置档案
   */
  function setActiveProfile(id) {
    return new Promise((resolve) => {
      chrome.storage.local.set({ activeProfileId: id }, resolve);
    });
  }

  /**
   * 保存设置
   */
  function saveSettings(settings) {
    return new Promise((resolve) => {
      chrome.storage.local.set({ settings }, resolve);
    });
  }

  /**
   * 初始化默认数据（首次安装时调用）
   */
  function initDefaults() {
    return new Promise((resolve) => {
      chrome.storage.local.get(['profiles'], (result) => {
        if (!result.profiles || Object.keys(result.profiles).length === 0) {
          chrome.storage.local.set({
            activeProfileId: 'default',
            profiles: { default: DEFAULT_PROFILE },
            settings: DEFAULT_SETTINGS
          }, resolve);
        } else {
          resolve();
        }
      });
    });
  }

  return {
    getActiveProfile,
    getSettings,
    saveProfile,
    deleteProfile,
    getAllProfiles,
    setActiveProfile,
    saveSettings,
    initDefaults,
    DEFAULT_SETTINGS
  };
})();

if (typeof window !== 'undefined') {
  window.FormStorage = FormStorage;
}
