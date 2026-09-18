/**
 * FuzzyMatcher - 模糊匹配核心算法
 * 采用 LCS + Jaccard + 包含奖励的三重评分机制
 */
const FuzzyMatcher = (() => {

  /**
   * 文本归一化：去除空格、标点、转小写
   */
  function normalize(str) {
    if (!str) return '';
    return str
      .toLowerCase()
      .replace(/[\s_\-\.·:：,，、!！?？()（）\[\]【】{}<>《》""''\"'\/\\|@#$%^&*+=~`]/g, '')
      .trim();
  }

  /**
   * 最长公共子序列长度（空间优化版）
   */
  function lcsLength(a, b) {
    if (!a || !b) return 0;
    const m = a.length, n = b.length;
    if (m > n) {
      [a, b] = [b, a];
    }
    const shortLen = Math.min(m, n);
    const longLen = Math.max(m, n);
    let prev = new Array(shortLen + 1).fill(0);
    let curr = new Array(shortLen + 1).fill(0);

    for (let j = 1; j <= longLen; j++) {
      for (let i = 1; i <= shortLen; i++) {
        if (a[i - 1] === b[j - 1]) {
          curr[i] = prev[i - 1] + 1;
        } else {
          curr[i] = Math.max(curr[i - 1], prev[i]);
        }
      }
      [prev, curr] = [curr, new Array(shortLen + 1).fill(0)];
    }
    return prev[shortLen];
  }

  /**
   * LCS 相似度：2 * lcs / (len(a) + len(b))
   */
  function lcsSimilarity(a, b) {
    const na = normalize(a);
    const nb = normalize(b);
    if (!na || !nb) return 0;
    const lcs = lcsLength(na, nb);
    return (2.0 * lcs) / (na.length + nb.length);
  }

  /**
   * Jaccard 字符集重叠度
   */
  function tokenOverlap(a, b) {
    const na = normalize(a);
    const nb = normalize(b);
    if (!na || !nb) return 0;
    const setA = new Set(na.split(''));
    const setB = new Set(nb.split(''));
    let intersection = 0;
    setA.forEach(ch => { if (setB.has(ch)) intersection++; });
    const union = new Set([...setA, ...setB]).size;
    return union === 0 ? 0 : intersection / union;
  }

  /**
   * 包含奖励：如果一方完全包含另一方，给予额外加分
   */
  function containmentBonus(a, b) {
    const na = normalize(a);
    const nb = normalize(b);
    if (!na || !nb) return 0;
    if (na.includes(nb) || nb.includes(na)) {
      const shorter = Math.min(na.length, nb.length);
      const longer = Math.max(na.length, nb.length);
      return 0.3 * (shorter / longer);
    }
    return 0;
  }

  /**
   * 综合匹配分数
   * @param {string} fieldContext - 页面表单元素的上下文文本
   * @param {string} fieldKey - 用户预设的匹配关键字
   * @returns {number} 0~1 之间的分数
   */
  function matchScore(fieldContext, fieldKey) {
    const lcsSim = lcsSimilarity(fieldContext, fieldKey);
    const tokenSim = tokenOverlap(fieldContext, fieldKey);
    const bonus = containmentBonus(fieldContext, fieldKey);
    return Math.min(1.0, 0.45 * lcsSim + 0.35 * tokenSim + bonus);
  }

  /**
   * 在字段列表中找到最佳匹配
   * @param {string} contextText - 表单元素上下文文本
   * @param {Array} fields - 用户预设字段列表 [{key, value}]
   * @param {number} sensitivity - 匹配阈值 (0~1)
   * @returns {Object|null} {field, score} 或 null
   */
  function findBestMatch(contextText, fields, sensitivity = 0.4) {
    let bestMatch = null;
    let bestScore = 0;

    for (const field of fields) {
      const score = matchScore(contextText, field.key);
      if (score > bestScore && score >= sensitivity) {
        bestScore = score;
        bestMatch = { field, score };
      }
    }

    return bestMatch;
  }

  /**
   * 返回所有候选匹配（按分数降序），用于显著性检查
   * @param {string} contextText - 表单元素上下文文本
   * @param {Array} fields - 用户预设字段列表
   * @returns {Array} [{field, score}] 按分数降序
   */
  function findAllCandidates(contextText, fields) {
    const candidates = [];
    for (const field of fields) {
      const score = matchScore(contextText, field.key);
      if (score > 0) {
        candidates.push({ field, score });
      }
    }
    candidates.sort((a, b) => b.score - a.score);
    return candidates;
  }

  /**
   * 计算匹配关键字的最低分数底线
   * 关键字越短，底线越高（避免短关键字误匹配）
   * @param {string} key - 匹配关键字
   * @returns {number} 最低分数
   */
  function minScoreFloor(key) {
    const len = normalize(key).length;
    if (len <= 2) return 0.75;
    if (len <= 4) return 0.60;
    if (len <= 6) return 0.45;
    if (len <= 8) return 0.38;
    return 0.30;
  }

  return { matchScore, findAllCandidates, minScoreFloor, normalize, lcsSimilarity };
})();

if (typeof window !== 'undefined') {
  window.FuzzyMatcher = FuzzyMatcher;
}
