export async function fetchApi(url, options = {}) {
  try {
    const response = await fetch(url, {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers
      },
      ...options
    });

    if (!response.ok) {
      throw new Error(`HTTP错误: ${response.status}`);
    }

    const contentType = response.headers.get('content-type');
    if (!contentType || !contentType.includes('application/json')) {
      const text = await response.text();
      throw new Error(`非JSON响应: ${text.substring(0, 200)}`);
    }

    return response.json();
  } catch (error) {
    console.error('API请求失败:', url, error);
    throw error;
  }
}

export async function fetchApiSafe(url, options = {}) {
  try {
    return await fetchApi(url, options);
  } catch (error) {
    console.error('API安全请求失败:', url, error);
    return { success: false, error: error.message };
  }
}

// ========== 预测 API 封装 ==========

/**
 * 基础预测（胜平负）
 */
export async function predictMatch(homeTeam, awayTeam, options = {}) {
  return fetchApi('/api/predict', {
    method: 'POST',
    body: JSON.stringify({ homeTeam, awayTeam, options })
  });
}

/**
 * 让球胜平负预测
 * @param {string} homeTeam - 主队
 * @param {string} awayTeam - 客队
 * @param {number} handicap - 让球数 (正=主让, 负=客让, 0=平手)
 * @param {Object} options - 其他选项
 */
export async function predictHandicap(homeTeam, awayTeam, handicap, options = {}) {
  return fetchApi('/api/predict/handicap', {
    method: 'POST',
    body: JSON.stringify({ homeTeam, awayTeam, handicap, options })
  });
}

/**
 * 精确比分预测 (T-006 v4)
 * @param {string} homeTeam - 主队
 * @param {string} awayTeam - 客队
 * @param {Object} scoreOdds - 可选: 比分赔率 { "1:0": 8.5, ... }
 * @param {Object} options - 其他选项
 */
export async function predictScore(homeTeam, awayTeam, scoreOdds = null, options = {}) {
  return fetchApi('/api/predict/score', {
    method: 'POST',
    body: JSON.stringify({ homeTeam, awayTeam, scoreOdds, options })
  });
}

/**
 * 批量预测
 */
export async function predictBatch(matches) {
  return fetchApi('/api/predict/batch', {
    method: 'POST',
    body: JSON.stringify({ matches })
  });
}

/**
 * 融合赔率预测
 */
export async function predictWithOdds(homeTeam, awayTeam, odds, options = {}) {
  return fetchApi('/api/predict/with-odds', {
    method: 'POST',
    body: JSON.stringify({ homeTeam, awayTeam, odds, options })
  });
}

/**
 * 获取球队列表
 */
export async function getTeams() {
  return fetchApi('/api/predict/teams');
}

/**
 * 获取预测历史
 */
export async function getPredictionHistory(limit = 20, offset = 0) {
  return fetchApi(`/api/predict/history?limit=${limit}&offset=${offset}`);
}