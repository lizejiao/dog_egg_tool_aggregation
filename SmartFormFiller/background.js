/**
 * Background Service Worker
 * 负责初始化默认数据、消息路由、角标管理
 */
importScripts('lib/storage.js');

// 安装/更新时初始化默认数据
chrome.runtime.onInstalled.addListener(() => {
  FormStorage.initDefaults().then(() => {
    console.log('[SmartFormFiller] 插件已安装，默认数据初始化完成');
  });
});

// 消息处理
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // 获取当前激活的配置档案
  if (message.type === 'GET_ACTIVE_PROFILE') {
    FormStorage.getActiveProfile().then(profile => {
      sendResponse({ profile });
    });
    return true; // 异步响应
  }

  // 填充完成，更新角标
  if (message.type === 'FILL_COMPLETE') {
    const count = message.count;
    const tabId = sender.tab?.id;
    if (count > 0 && tabId) {
      chrome.action.setBadgeText({ text: String(count), tabId });
      chrome.action.setBadgeBackgroundColor({ color: '#4CAF50', tabId });
      // 3秒后清除角标
      setTimeout(() => {
        chrome.action.setBadgeText({ text: '', tabId });
      }, 3000);
    }
    sendResponse({ success: true });
    return true;
  }

  // 获取所有配置（供 popup 使用）
  if (message.type === 'GET_ALL_PROFILES') {
    FormStorage.getAllProfiles().then(data => {
      sendResponse(data);
    });
    return true;
  }

  // 保存配置档案
  if (message.type === 'SAVE_PROFILE') {
    FormStorage.saveProfile(message.profile).then(() => {
      sendResponse({ success: true });
    });
    return true;
  }

  // 删除配置档案
  if (message.type === 'DELETE_PROFILE') {
    FormStorage.deleteProfile(message.id).then(() => {
      sendResponse({ success: true });
    });
    return true;
  }

  // 设置激活的配置
  if (message.type === 'SET_ACTIVE_PROFILE') {
    FormStorage.setActiveProfile(message.id).then(() => {
      sendResponse({ success: true });
    });
    return true;
  }

  // 获取设置
  if (message.type === 'GET_SETTINGS') {
    FormStorage.getSettings().then(settings => {
      sendResponse({ settings });
    });
    return true;
  }

  // 保存设置
  if (message.type === 'SAVE_SETTINGS') {
    FormStorage.saveSettings(message.settings).then(() => {
      sendResponse({ success: true });
    });
    return true;
  }
});
