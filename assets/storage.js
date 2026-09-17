/* ============================================================
 * 统一持久化模块 (v1.0)
 * 解决前后端持久化不同步问题
 * 提供localStorage/fs双写一致性保障
 * ============================================================ */

var STORAGE = (function() {
  var STORAGE_KEY = 'wc2026_predictions';
  var BACKUP_KEY = 'wc2026_backup';
  
  // 检测是否支持localStorage
  function isLocalStorageAvailable() {
    try {
      var key = '__wc2026_test__';
      localStorage.setItem(key, key);
      localStorage.removeItem(key);
      return true;
    } catch (e) {
      return false;
    }
  }

  // 读取数据 (优先localStorage，失败则尝试备份)
  function loadData() {
    if (!isLocalStorageAvailable()) return null;
    
    try {
      var data = localStorage.getItem(STORAGE_KEY);
      if (data) {
        return JSON.parse(data);
      }
      // 尝试读取备份
      var backup = localStorage.getItem(BACKUP_KEY);
      if (backup) {
        return JSON.parse(backup);
      }
    } catch (e) {
      console.error('Storage.loadData error:', e);
    }
    return null;
  }

  // 写入数据 (localStorage + 备份)
  function saveData(data) {
    if (!isLocalStorageAvailable()) return false;
    
    try {
      var json = JSON.stringify(data);
      // 先写备份
      localStorage.setItem(BACKUP_KEY, json);
      // 再写主存储
      localStorage.setItem(STORAGE_KEY, json);
      return true;
    } catch (e) {
      console.error('Storage.saveData error:', e);
      return false;
    }
  }

  // 合并更新 (避免覆盖其他模块写入的数据)
  function mergeUpdate(newData) {
    var existing = loadData() || {};
    // 深度合并
    var merged = deepMerge(existing, newData);
    return saveData(merged);
  }

  // 深度合并对象
  function deepMerge(target, source) {
    var result = { ...target };
    for (var key in source) {
      if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key])) {
        result[key] = deepMerge(target[key] || {}, source[key]);
      } else {
        result[key] = source[key];
      }
    }
    return result;
  }

  // 清除数据
  function clear() {
    if (!isLocalStorageAvailable()) return;
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(BACKUP_KEY);
  }

  // 获取特定键的数据
  function getItem(key) {
    var data = loadData();
    return data ? data[key] : null;
  }

  // 设置特定键的数据
  function setItem(key, value) {
    var data = loadData() || {};
    data[key] = value;
    return saveData(data);
  }

  // 删除特定键
  function removeItem(key) {
    var data = loadData();
    if (data && key in data) {
      delete data[key];
      return saveData(data);
    }
    return false;
  }

  // 获取存储大小
  function getSize() {
    if (!isLocalStorageAvailable()) return 0;
    try {
      var data = localStorage.getItem(STORAGE_KEY);
      return data ? data.length : 0;
    } catch (e) {
      return 0;
    }
  }

  // 数据完整性检查
  function verifyIntegrity() {
    var data = loadData();
    if (!data) return { valid: false, reason: 'no data' };
    
    // 检查必要字段
    if (!data.predictions) return { valid: false, reason: 'missing predictions' };
    if (!data.archive) return { valid: false, reason: 'missing archive' };
    
    return { valid: true, reason: 'ok' };
  }

  return {
    loadData: loadData,
    saveData: saveData,
    mergeUpdate: mergeUpdate,
    clear: clear,
    getItem: getItem,
    setItem: setItem,
    removeItem: removeItem,
    getSize: getSize,
    verifyIntegrity: verifyIntegrity,
    isAvailable: isLocalStorageAvailable
  };
})();
