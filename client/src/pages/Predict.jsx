import React, { useState, useEffect } from 'react';
import { 
  Card, Row, Col, Select, Button, Spin, Typography, Tag, Table, 
  Tabs, Form, InputNumber, Divider, Alert, Space, Statistic,
  Modal, Input, Collapse, message, List, Tooltip
} from 'antd';
import { 
  TrophyOutlined, ThunderboltOutlined, DashboardOutlined,
  LineChartOutlined, CalculatorOutlined, SyncOutlined,
  RocketOutlined, TeamOutlined, ClusterOutlined, SafetyOutlined
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { fetchApiSafe, fetchApi, predictHandicap, predictScore } from '../utils/api';

const { Title, Text, Paragraph } = Typography;

function Predict() {
  const [teams, setTeams] = useState([]);
  const [loading, setLoading] = useState(false);
  const [prediction, setPrediction] = useState(null);
  const [odds, setOdds] = useState({ win: 1.45, draw: 4.05, lose: 5.20 });
  const [selectedLeague, setSelectedLeague] = useState('epl');
  const [leagues, setLeagues] = useState([
    { code: 'epl', name: '英超' },
    { code: 'la_liga', name: '西甲' },
    { code: 'serie_a', name: '意甲' },
    { code: 'bundesliga', name: '德甲' },
    { code: 'ligue_1', name: '法甲' }
  ]);

  const [form] = Form.useForm();

  useEffect(() => {
    fetchApiSafe('/api/predict/teams')
      .then(data => {
        if (data.success) {
          setTeams(data.data);
        }
      });
  }, []);

  const [error, setError] = useState(null);

  // 批量预测状态
  const [batchModalVisible, setBatchModalVisible] = useState(false);
  const [batchMatches, setBatchMatches] = useState([]);
  const [batchResults, setBatchResults] = useState(null);
  const [batchLoading, setBatchLoading] = useState(false);

  // 阵容预测状态
  const [useLineup, setUseLineup] = useState(false);
  const [homeLineupText, setHomeLineupText] = useState('');
  const [awayLineupText, setAwayLineupText] = useState('');

  // 让球预测状态
  const [handicapValue, setHandicapValue] = useState(-1);
  const [handicapPrediction, setHandicapPrediction] = useState(null);
  const [handicapLoading, setHandicapLoading] = useState(false);

  // 比分预测状态
  const [scorePrediction, setScorePrediction] = useState(null);
  const [scoreLoading, setScoreLoading] = useState(false);

  // 预测历史持久化
  const [history, setHistory] = useState(() => {
    try {
      const saved = localStorage.getItem('prediction_history');
      return saved ? JSON.parse(saved) : [];
    } catch { return []; }
  });

  // 获取认证请求头（本地个人使用：可选，无 token 也可持久化到服务器）
  const getAuthHeaders = () => {
    const token = localStorage.getItem('token');
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  // 启动时尝试从服务器加载历史（无论是否登录），失败则保留 localStorage 缓存
  useEffect(() => {
    (async () => {
      try {
        const data = await fetchApi('/api/predict/history?limit=20', {
          headers: getAuthHeaders()
        });
        if (data.success && Array.isArray(data.data)) {
          setHistory(data.data);
        }
      } catch (e) {
        console.warn('从服务器加载预测历史失败，使用本地缓存:', e.message);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 批量预测：添加比赛
  const addBatchMatch = () => {
    setBatchMatches([...batchMatches, { homeTeam: '', awayTeam: '', league: 'epl' }]);
  };

  // 批量预测：执行
  const handleBatchPredict = async () => {
    try {
      setBatchLoading(true);
      const validMatches = batchMatches.filter(m => m.homeTeam && m.awayTeam);
      if (validMatches.length === 0) {
        message.warning('请至少添加一场比赛');
        return;
      }
      const data = await fetchApi('/api/predict/batch', {
        method: 'POST',
        body: JSON.stringify({ matches: validMatches })
      });
      if (data.success) {
        setBatchResults(data.data);
        message.success(`批量预测完成: ${data.count}场比赛`);
      } else {
        message.error(data.error?.message || '批量预测失败');
      }
    } catch (err) {
      message.error('批量预测请求失败: ' + err.message);
    } finally {
      setBatchLoading(false);
    }
  };

  // 阵容预测
  const handlePredictWithLineup = async () => {
    try {
      setLoading(true);
      setError(null);
      const values = await form.validateFields();

      // 解析阵容文本（每行一个评分，如 "7.5,8.0,6.5,..."）
      const parseLineup = (text) => {
        if (!text.trim()) return null;
        return text.split(/[\n,，]/).map(s => {
          const rating = parseFloat(s.trim());
          return { rating: isNaN(rating) ? 7.0 : rating, keyContribution: 0 };
        }).filter(p => p.rating > 0);
      };

      const homeLineup = parseLineup(homeLineupText);
      const awayLineup = parseLineup(awayLineupText);

      if (!homeLineup || homeLineup.length < 1 || !awayLineup || awayLineup.length < 1) {
        message.warning('请输入双方阵容评分（每行一个数值或逗号分隔）');
        setLoading(false);
        return;
      }

      const data = await fetchApi('/api/predict/with-lineup', {
        method: 'POST',
        body: JSON.stringify({
          homeTeam: values.homeTeam,
          awayTeam: values.awayTeam,
          league: selectedLeague,
          homeLineup,
          awayLineup,
          options: { venue: values.venue || 'sea_level', neutral: values.neutral || false }
        })
      });

      if (data.success) {
        setPrediction(data.data);
        // 保存到历史（登录同步服务器，未登录降级 localStorage）
        saveToHistory(data.data);
        message.success('阵容预测完成');
      } else {
        setError(data.error?.message || '阵容预测失败');
      }
    } catch (err) {
      setError('阵容预测失败: ' + err.message);
    } finally {
      setLoading(false);
    }
  };

  // 保存预测到历史（本地个人使用：直接同步服务器，失败降级 localStorage）
  const saveToHistory = async (pred) => {
    if (!pred) return;
    // 本地立即更新（保证 UI 响应）
    const localEntry = { ...pred, savedAt: Date.now() };
    const newHistory = [localEntry, ...history].slice(0, 20);
    setHistory(newHistory);
    try { localStorage.setItem('prediction_history', JSON.stringify(newHistory)); } catch (e) {}

    // 尝试同步到服务器（带或不带 token 均可）
    try {
      const data = await fetchApi('/api/predict/history', {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ prediction: pred, league: selectedLeague })
      });
      if (data.success && data.data) {
        const merged = [data.data, ...history].slice(0, 20);
        setHistory(merged);
        try { localStorage.setItem('prediction_history', JSON.stringify(merged)); } catch (e) {}
      }
    } catch (e) {
      console.warn('保存到服务器失败，仅保留本地:', e.message);
    }
  };

  // 恢复历史预测
  const restoreFromHistory = (index) => {
    if (history[index]) {
      setPrediction(history[index]);
      message.success('已恢复历史预测');
    }
  };

  // 清空历史（同步服务器+清本地缓存，失败降级仅清本地）
  const clearHistory = async () => {
    setHistory([]);
    try { localStorage.removeItem('prediction_history'); } catch (e) {}
    try {
      await fetchApi('/api/predict/history', {
        method: 'DELETE',
        headers: getAuthHeaders()
      });
    } catch (e) {
      console.warn('清空服务器历史失败:', e.message);
    }
    message.success('历史记录已清空');
  };

  const handlePredict = async () => {
    try {
      setLoading(true);
      setError(null);
      const values = await form.validateFields();
      
      const data = await fetchApi('/api/predict', {
        method: 'POST',
        body: JSON.stringify({
          homeTeam: values.homeTeam,
          awayTeam: values.awayTeam,
          league: selectedLeague,
          options: {
            venue: values.venue || 'sea_level',
            neutral: values.neutral || false
          }
        })
      });

      if (data.success) {
        if (data.data && data.data.lambda && data.data.lambda.home !== null) {
          setPrediction(data.data);
          // 自动保存到历史（登录同步服务器，未登录降级 localStorage）
          saveToHistory(data.data);
        } else {
          setError('预测计算失败，未能获取有效的预测结果');
          console.error('预测结果无效:', data.data);
        }
      } else {
        setError(data.error?.message || '预测失败');
      }
    } catch (err) {
      console.error('预测失败:', err);
      setError('网络请求失败，请检查服务器连接');
    } finally {
      setLoading(false);
    }
  };

  const handlePredictWithOdds = async () => {
    try {
      setLoading(true);
      const values = await form.validateFields();
      
      const data = await fetchApi('/api/predict/with-odds', {
        method: 'POST',
        body: JSON.stringify({
          homeTeam: values.homeTeam,
          awayTeam: values.awayTeam,
          league: selectedLeague,
          odds: odds,
          options: {
            venue: values.venue || 'sea_level',
            neutral: values.neutral || false
          }
        })
      });

      if (data.success) {
        setPrediction(data.data);
      }
    } catch (err) {
      console.error('预测失败:', err);
    } finally {
      setLoading(false);
    }
  };

  // 让球胜平负预测
  const handlePredictHandicap = async () => {
    try {
      setHandicapLoading(true);
      const values = await form.validateFields();

      const data = await predictHandicap(values.homeTeam, values.awayTeam, handicapValue, {
        venue: values.venue || 'sea_level',
        neutral: values.neutral || false
      });

      if (data.success) {
        setHandicapPrediction(data.data);
        message.success('让球预测完成');
      } else {
        message.error(data.error?.message || '让球预测失败');
      }
    } catch (err) {
      console.error('让球预测失败:', err);
      message.error('让球预测请求失败');
    } finally {
      setHandicapLoading(false);
    }
  };

  // 精确比分预测 (T-006 v4)
  const handlePredictScore = async () => {
    try {
      setScoreLoading(true);
      const values = await form.validateFields();

      const data = await predictScore(values.homeTeam, values.awayTeam, null, {
        venue: values.venue || 'sea_level',
        neutral: values.neutral || false
      });

      if (data.success) {
        setScorePrediction(data.data);
        message.success('比分预测完成 (T-006 v4)');
      } else {
        message.error(data.error?.message || '比分预测失败');
      }
    } catch (err) {
      console.error('比分预测失败:', err);
      message.error('比分预测请求失败');
    } finally {
      setScoreLoading(false);
    }
  };

  const winDrawLoseChartOption = prediction && prediction.predictions ? {
    title: { text: '胜平负概率', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'item', formatter: '{b}: {c}% ({d}%)', textStyle: { color: '#f1f5f9' } },
    legend: { bottom: '5%', left: 'center', textStyle: { color: '#94a3b8', fontSize: 12 } },
    series: [{
      name: '概率',
      type: 'pie',
      radius: ['35%', '65%'],
      avoidLabelOverlap: false,
      itemStyle: {
        borderRadius: 10,
        borderColor: '#1e293b',
        borderWidth: 2
      },
      label: { show: true, formatter: '{b}: {d}%', color: '#94a3b8' },
      data: [
        { value: typeof prediction.predictions.winDrawLose.win === 'number' ? (prediction.predictions.winDrawLose.win * 100).toFixed(1) : 0, name: '主胜', itemStyle: { color: '#10b981' } },
        { value: typeof prediction.predictions.winDrawLose.draw === 'number' ? (prediction.predictions.winDrawLose.draw * 100).toFixed(1) : 0, name: '平局', itemStyle: { color: '#f59e0b' } },
        { value: typeof prediction.predictions.winDrawLose.lose === 'number' ? (prediction.predictions.winDrawLose.lose * 100).toFixed(1) : 0, name: '客胜', itemStyle: { color: '#ef4444' } }
      ]
    }]
  } : {};

  const scoreChartOption = prediction ? {
    title: { text: '比分概率分布', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '10%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: prediction.predictions.topScores.slice(0, 10).map(s => s.score),
      axisLabel: { color: '#94a3b8', fontSize: 11, rotate: 45 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: { 
      type: 'value', 
      name: '概率 (%)',
      axisLabel: { color: '#94a3b8', fontSize: 11 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      data: prediction.predictions.topScores.slice(0, 10).map(s => (s.probability * 100).toFixed(2)),
      type: 'bar',
      itemStyle: { 
        color: 'linear-gradient(135deg, #3b82f6, #2563eb)',
        borderRadius: [6, 6, 0, 0]
      }
    }]
  } : {};

  const totalGoalsChartOption = prediction ? {
    title: { text: '总进球分布', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '10%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: ['0球', '1球', '2球', '3球', '4球', '5球', '6球', '7+球'],
      axisLabel: { color: '#94a3b8', fontSize: 11 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: { 
      type: 'value', 
      name: '概率 (%)',
      axisLabel: { color: '#94a3b8', fontSize: 11 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      data: Object.entries(prediction.predictions.totalGoals.distribution).map(([k, v]) => (v * 100).toFixed(2)),
      type: 'line',
      smooth: true,
      itemStyle: { color: '#8b5cf6' },
      lineStyle: { width: 3 },
      areaStyle: { color: '#8b5cf6', opacity: 0.2 },
      symbol: 'circle',
      symbolSize: 8
    }]
  } : {};

  const lambdaChartOption = prediction ? {
    title: { text: '进攻效率对比 (Lambda)', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '10%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: [prediction.match.homeTeam.name, prediction.match.awayTeam.name],
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: { 
      type: 'value', 
      name: 'Lambda值',
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      data: [prediction.lambda.home.toFixed(3), prediction.lambda.away.toFixed(3)],
      type: 'bar',
      itemStyle: {
        color: (params) => params.dataIndex === 0 ? '#10b981' : '#ef4444',
        borderRadius: [8, 8, 0, 0]
      },
      barWidth: '50%'
    }]
  } : {};

  // 模型分解：6模型独立胜平负概率对比
  const modelBreakdownChartOption = prediction && prediction.ensemble && prediction.ensemble.components ? {
    title: { text: '6模型独立预测对比', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { color: '#f1f5f9' } },
    legend: { data: ['主胜', '平局', '客胜'], bottom: '2%', textStyle: { color: '#94a3b8', fontSize: 12 } },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '12%', containLabel: true },
    xAxis: {
      type: 'category',
      data: Object.keys(prediction.ensemble.components).map(k => {
        const names = { poisson: 'Poisson', dixonCole: 'DixonCole', ssm: 'SSM', xgboost: 'XGBoost', lightgbm: 'LightGBM', elo: 'Elo' };
        return names[k] || k;
      }),
      axisLabel: { color: '#94a3b8', fontSize: 11 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: {
      type: 'value', name: '概率 (%)', max: 100,
      axisLabel: { color: '#94a3b8', fontSize: 11, formatter: '{value}%' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [
      { name: '主胜', type: 'bar', data: Object.values(prediction.ensemble.components).map(c => (c.winA * 100).toFixed(1)), itemStyle: { color: '#10b981', borderRadius: [4, 4, 0, 0] } },
      { name: '平局', type: 'bar', data: Object.values(prediction.ensemble.components).map(c => (c.draw * 100).toFixed(1)), itemStyle: { color: '#f59e0b', borderRadius: [4, 4, 0, 0] } },
      { name: '客胜', type: 'bar', data: Object.values(prediction.ensemble.components).map(c => (c.winB * 100).toFixed(1)), itemStyle: { color: '#ef4444', borderRadius: [4, 4, 0, 0] } }
    ]
  } : {};

  // 模型分解：集成权重饼图
  const ensembleWeightsChartOption = prediction && prediction.ensemble && prediction.ensemble.weights ? {
    title: { text: '集成权重分布', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'item', formatter: '{b}: {d}%', textStyle: { color: '#f1f5f9' } },
    legend: { bottom: '2%', left: 'center', textStyle: { color: '#94a3b8', fontSize: 11 } },
    series: [{
      type: 'pie', radius: ['35%', '65%'],
      itemStyle: { borderRadius: 8, borderColor: '#1e293b', borderWidth: 2 },
      label: { show: true, formatter: '{b}: {d}%', color: '#94a3b8' },
      data: Object.entries(prediction.ensemble.weights).map(([k, v]) => {
        const names = { poisson: 'Poisson', dixonCole: 'DixonCole', ssm: 'SSM', xgboost: 'XGBoost', lightgbm: 'LightGBM', elo: 'Elo' };
        const colors = { poisson: '#3b82f6', dixonCole: '#10b981', ssm: '#8b5cf6', xgboost: '#f59e0b', lightgbm: '#ef4444', elo: '#06b6d4' };
        return { value: (v * 100).toFixed(1), name: names[k] || k, itemStyle: { color: colors[k] || '#64748b' } };
      })
    }]
  } : {};

  // 模型分解表格列定义
  const modelBreakdownColumns = [
    { title: '模型', dataIndex: 'name', key: 'name', render: (t) => <span style={{ color: '#f1f5f9', fontWeight: 'bold' }}>{t}</span> },
    { title: '主胜', dataIndex: 'win', key: 'win', render: (v) => <span style={{ color: '#10b981' }}>{(v * 100).toFixed(1)}%</span> },
    { title: '平局', dataIndex: 'draw', key: 'draw', render: (v) => <span style={{ color: '#f59e0b' }}>{(v * 100).toFixed(1)}%</span> },
    { title: '客胜', dataIndex: 'lose', key: 'lose', render: (v) => <span style={{ color: '#ef4444' }}>{(v * 100).toFixed(1)}%</span> },
    { title: '权重', dataIndex: 'weight', key: 'weight', render: (v) => <Tag color="blue">{(v * 100).toFixed(1)}%</Tag> },
    { title: '与集成差异', key: 'diff', render: (_, r) => {
      if (!prediction?.predictions?.winDrawLose) return '-';
      const diff = Math.abs(r.win - prediction.predictions.winDrawLose.win) + Math.abs(r.draw - prediction.predictions.winDrawLose.draw) + Math.abs(r.lose - prediction.predictions.winDrawLose.lose);
      const level = diff < 0.1 ? <Tag color="green">一致</Tag> : diff < 0.3 ? <Tag color="orange">轻微分歧</Tag> : <Tag color="red">显著分歧</Tag>;
      return level;
    }}
  ];

  const modelBreakdownData = prediction && prediction.ensemble && prediction.ensemble.components
    ? Object.entries(prediction.ensemble.components).map(([k, v]) => {
        const names = { poisson: 'Poisson', dixonCole: 'DixonCole', ssm: 'SSM', xgboost: 'XGBoost', lightgbm: 'LightGBM', elo: 'Elo' };
        return { key: k, name: names[k] || k, win: v.winA, draw: v.draw, lose: v.winB, weight: v.weight };
      })
    : [];

  const scoreColumns = [
    { title: '比分', dataIndex: 'score', key: 'score', render: (text) => <span style={{ color: '#f1f5f9', fontWeight: 'bold' }}>{text}</span> },
    { title: '概率', dataIndex: 'probability', key: 'probability', render: (v) => <span style={{ color: '#94a3b8' }}>{(v * 100).toFixed(2)}%</span> },
    { title: '推荐', key: 'recommend', render: (_, record) => {
      if (record.probability > 0.15) return <Tag color="green">强烈推荐</Tag>;
      if (record.probability > 0.10) return <Tag color="blue">推荐</Tag>;
      return <Tag color="default">可选</Tag>;
    }}
  ];

  return (
    <div className="fade-in">
      <div style={{ marginBottom: 24 }}>
        <Title level={2} style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <TrophyOutlined style={{ color: '#3b82f6' }} />
          <span style={{ background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>比赛预测</span>
        </Title>
        <Paragraph style={{ margin: '8px 0 0', color: '#94a3b8' }}>
          选择比赛双方，系统将基于<strong style={{ color: '#3b82f6' }}>Poisson分布模型</strong>计算胜平负、比分、让球盘、总进球等预测结果。
        </Paragraph>
      </div>

      <Card style={{ marginBottom: 24 }}>
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={6}>
              <Form.Item label="联赛" rules={[{ required: true }]}>
                <Select 
                  value={selectedLeague}
                  onChange={setSelectedLeague}
                  style={{ width: '100%' }}
                  options={leagues.map(l => ({ value: l.code, label: l.name }))}
                />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="homeTeam" label="主队" rules={[{ required: true }]}>
                <Select 
                  style={{ width: '100%' }} 
                  placeholder="选择主队"
                  showSearch
                  filterOption={(input, option) => 
                    option.children.toLowerCase().indexOf(input.toLowerCase()) >= 0
                  }
                >
                  {teams.map(team => (
                    <Select.Option key={team.key} value={team.key}>
                      {team.name}
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="awayTeam" label="客队" rules={[{ required: true }]}>
                <Select 
                  style={{ width: '100%' }} 
                  placeholder="选择客队"
                  showSearch
                  filterOption={(input, option) => 
                    option.children.toLowerCase().indexOf(input.toLowerCase()) >= 0
                  }
                >
                  {teams.map(team => (
                    <Select.Option key={team.key} value={team.key}>
                      {team.name}
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="venue" label="场地">
                <Select style={{ width: '100%' }} defaultValue="sea_level">
                  <Select.Option value="sea_level">平原</Select.Option>
                  <Select.Option value="altitude">高原</Select.Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={6} style={{ display: 'flex', alignItems: 'flex-end' }}>
              <Form.Item name="neutral" label="中立场地">
                <Select style={{ width: '100%' }} defaultValue={false}>
                  <Select.Option value={false}>否</Select.Option>
                  <Select.Option value={true}>是</Select.Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={12} style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'flex-end' }}>
              <Space wrap>
                <Button 
                  type="primary" 
                  icon={<CalculatorOutlined />} 
                  onClick={handlePredict}
                  loading={loading}
                  size="large"
                >
                  开始预测
                </Button>
                <Button 
                  icon={<SyncOutlined />} 
                  onClick={handlePredictWithOdds}
                  loading={loading}
                  size="large"
                >
                  融合赔率
                </Button>
                <Button
                  icon={<TeamOutlined />}
                  onClick={handlePredictWithLineup}
                  loading={loading}
                  size="large"
                >
                  阵容预测
                </Button>
                <Button
                  icon={<SafetyOutlined />}
                  onClick={handlePredictHandicap}
                  loading={handicapLoading}
                  size="large"
                >
                  让球预测
                </Button>
                <Button
                  icon={<TrophyOutlined />}
                  onClick={handlePredictScore}
                  loading={scoreLoading}
                  size="large"
                >
                  比分预测
                </Button>
                <Button
                  icon={<ClusterOutlined />}
                  onClick={() => { setBatchModalVisible(true); setBatchResults(null); }}
                  size="large"
                >
                  批量预测
                </Button>
              </Space>
            </Col>
          </Row>
        </Form>
      </Card>

      {/* 让球盘口输入 */}
      <Card title="让球胜平负预测" style={{ marginBottom: 24 }}>
        <Row gutter={16} align="middle">
          <Col span={6}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ color: '#94a3b8' }}>让球数</span>
              <InputNumber
                value={handicapValue}
                onChange={(v) => setHandicapValue(v || 0)}
                step={0.5}
                min={-5}
                max={5}
                style={{ width: 100 }}
              />
              <span style={{ color: '#64748b', fontSize: 12 }}>
                {handicapValue > 0 ? `主队让${handicapValue}球` : handicapValue < 0 ? `客队让${Math.abs(handicapValue)}球` : '平手'}
              </span>
            </div>
          </Col>
          <Col span={18}>
            {handicapPrediction && handicapPrediction.handicap && (
              <Row gutter={16}>
                <Col span={8}>
                  <Statistic title="让球主胜" value={(handicapPrediction.handicap.prediction.win * 100).toFixed(1)} suffix="%" valueStyle={{ color: '#10b981' }}/>
                </Col>
                <Col span={8}>
                  <Statistic title="走水" value={(handicapPrediction.handicap.prediction.draw * 100).toFixed(1)} suffix="%" valueStyle={{ color: '#f59e0b' }}/>
                </Col>
                <Col span={8}>
                  <Statistic title="让球客胜" value={(handicapPrediction.handicap.prediction.lose * 100).toFixed(1)} suffix="%" valueStyle={{ color: '#ef4444' }}/>
                </Col>
              </Row>
            )}
          </Col>
        </Row>
      </Card>

      {/* T-006 v4 比分预测结果 */}
      {scorePrediction && scorePrediction.scorePrediction && (
        <Card title={
          <Space>
            <TrophyOutlined />
            <span>精确比分预测 (T-006 v4)</span>
            <Tag color="blue">{scorePrediction.scorePrediction.scoreModel}</Tag>
          </Space>
        } style={{ marginBottom: 24 }}>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={8}>
              <Statistic
                title="最可能比分"
                value={scorePrediction.scorePrediction.topScores[0]?.score || '-'}
                valueStyle={{ color: '#3b82f6', fontSize: 32 }}
              />
            </Col>
            <Col span={8}>
              <Statistic
                title="精确命中概率"
                value={(scorePrediction.scorePrediction.exactProbability * 100).toFixed(2)}
                suffix="%"
                valueStyle={{ color: '#10b981' }}
              />
            </Col>
            <Col span={8}>
              <Statistic
                title="1球内命中率"
                value={(scorePrediction.scorePrediction.within1Probability * 100).toFixed(2)}
                suffix="%"
                valueStyle={{ color: '#8b5cf6' }}
              />
            </Col>
          </Row>
          <Divider />
          <Title level={5}>Top 10 比分概率</Title>
          <Table
            dataSource={scorePrediction.scorePrediction.topScores}
            columns={[
              { title: '排名', render: (_, __, i) => i + 1, width: 60 },
              { title: '比分', dataIndex: 'score', width: 100 },
              { title: '概率', dataIndex: 'probability', render: v => `${(v * 100).toFixed(2)}%` },
              { title: '概率条', render: (_, r) => (
                <div style={{ width: '100%', height: 20, background: '#1e293b', borderRadius: 4 }}>
                  <div style={{ width: `${r.probability * 100 * 3}%`, height: '100%', background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)', borderRadius: 4 }} />
                </div>
              )}
            ]}
            pagination={false}
            size="small"
            rowKey="score"
          />
        </Card>
      )}

      <Card title="赔率数据（可选）" style={{ marginBottom: 24 }}>
        <Row gutter={16}>
          <Col span={8}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ color: '#94a3b8', width: 40 }}>主胜</span>
              <InputNumber 
                value={odds.win} 
                onChange={(v) => setOdds({...odds, win: v})}
                min={1} max={100} step={0.01}
                style={{ width: '100%' }}
                formatter={(value) => typeof value === 'number' ? value.toFixed(2) : ''}
              />
            </div>
          </Col>
          <Col span={8}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ color: '#94a3b8', width: 40 }}>平局</span>
              <InputNumber 
                value={odds.draw} 
                onChange={(v) => setOdds({...odds, draw: v})}
                min={1} max={100} step={0.01}
                style={{ width: '100%' }}
                formatter={(value) => typeof value === 'number' ? value.toFixed(2) : ''}
              />
            </div>
          </Col>
          <Col span={8}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ color: '#94a3b8', width: 40 }}>客胜</span>
              <InputNumber 
                value={odds.lose} 
                onChange={(v) => setOdds({...odds, lose: v})}
                min={1} max={100} step={0.01}
                style={{ width: '100%' }}
                formatter={(value) => typeof value === 'number' ? value.toFixed(2) : ''}
              />
            </div>
          </Col>
        </Row>
      </Card>

      {/* 阵容输入区（可选） */}
      <Card title={<span><TeamOutlined /> 首发阵容评分（可选 - 用于阵容预测）</span>} style={{ marginBottom: 24 }}>
        <Alert 
          type="info" 
          message="输入双方首发11人的球员评分（如 7.5, 8.0, 6.5, ...），点击上方「阵容预测」按钮执行"
          style={{ marginBottom: 16 }}
        />
        <Row gutter={16}>
          <Col span={12}>
            <Text style={{ color: '#94a3b8', display: 'block', marginBottom: 8 }}>主队阵容评分（逗号或换行分隔）</Text>
            <Input.TextArea
              value={homeLineupText}
              onChange={(e) => setHomeLineupText(e.target.value)}
              placeholder="7.5, 8.0, 6.5, 7.0, 8.2, 6.8, 7.3, 7.8, 6.9, 7.1, 7.4"
              rows={3}
            />
          </Col>
          <Col span={12}>
            <Text style={{ color: '#94a3b8', display: 'block', marginBottom: 8 }}>客队阵容评分（逗号或换行分隔）</Text>
            <Input.TextArea
              value={awayLineupText}
              onChange={(e) => setAwayLineupText(e.target.value)}
              placeholder="7.2, 7.8, 6.9, 7.5, 8.0, 7.0, 6.8, 7.6, 7.1, 6.9, 7.3"
              rows={3}
            />
          </Col>
        </Row>
      </Card>

      {/* 预测历史记录 */}
      {history.length > 0 && (
        <Card 
          title={<span><DashboardOutlined /> 预测历史（最近 {history.length} 条）</span>} 
          style={{ marginBottom: 24 }}
          extra={<Button size="small" danger onClick={clearHistory}>清空历史</Button>}
        >
          <List
            size="small"
            dataSource={history.slice(0, 5)}
            renderItem={(item, index) => (
              <List.Item
                actions={[
                  <Button size="small" type="link" onClick={() => restoreFromHistory(index)}>恢复</Button>
                ]}
              >
                <List.Item.Meta
                  title={`${item.match?.homeTeam?.name || '未知'} vs ${item.match?.awayTeam?.name || '未知'}`}
                  description={
                    <Space size="large">
                      <span style={{ color: '#10b981' }}>主胜 {(item.predictions?.winDrawLose?.win * 100).toFixed(1)}%</span>
                      <span style={{ color: '#f59e0b' }}>平局 {(item.predictions?.winDrawLose?.draw * 100).toFixed(1)}%</span>
                      <span style={{ color: '#ef4444' }}>客胜 {(item.predictions?.winDrawLose?.lose * 100).toFixed(1)}%</span>
                      <span style={{ color: '#64748b' }}>{new Date(item.savedAt).toLocaleString()}</span>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        </Card>
      )}

      {/* 批量预测模态框 */}
      <Modal
        title="批量预测"
        open={batchModalVisible}
        onCancel={() => setBatchModalVisible(false)}
        footer={[
          <Button key="cancel" onClick={() => setBatchModalVisible(false)}>关闭</Button>,
          <Button key="add" icon={<ClusterOutlined />} onClick={addBatchMatch}>添加比赛</Button>,
          <Button key="predict" type="primary" loading={batchLoading} onClick={handleBatchPredict}>执行批量预测</Button>,
        ]}
        width={800}
      >
        {batchResults ? (
          <div>
            <Alert type="success" message={`批量预测完成，共 ${batchResults.length || batchResults.length} 场比赛`} style={{ marginBottom: 16 }} />
            <Table
              dataSource={Array.isArray(batchResults) ? batchResults.map((r, i) => ({ ...r, key: i })) : []}
              columns={[
                { title: '主队', dataIndex: ['match', 'homeTeam', 'name'], key: 'home' },
                { title: '客队', dataIndex: ['match', 'awayTeam', 'name'], key: 'away' },
                { title: '主胜', key: 'win', render: (_, r) => <span style={{ color: '#10b981' }}>{(r.predictions?.winDrawLose?.win * 100).toFixed(1)}%</span> },
                { title: '平局', key: 'draw', render: (_, r) => <span style={{ color: '#f59e0b' }}>{(r.predictions?.winDrawLose?.draw * 100).toFixed(1)}%</span> },
                { title: '客胜', key: 'lose', render: (_, r) => <span style={{ color: '#ef4444' }}>{(r.predictions?.winDrawLose?.lose * 100).toFixed(1)}%</span> },
              ]}
              pagination={false}
              size="small"
            />
          </div>
        ) : (
          <div>
            <Alert type="info" message="添加多场比赛进行批量预测（需登录认证）" style={{ marginBottom: 16 }} />
            {batchMatches.length === 0 ? (
              <Button type="dashed" block icon={<ClusterOutlined />} onClick={addBatchMatch}>添加第一场比赛</Button>
            ) : (
              batchMatches.map((match, index) => (
                <Row key={index} gutter={8} style={{ marginBottom: 8 }}>
                  <Col span={10}>
                    <Input
                      placeholder="主队名称"
                      value={match.homeTeam}
                      onChange={(e) => {
                        const newMatches = [...batchMatches];
                        newMatches[index].homeTeam = e.target.value;
                        setBatchMatches(newMatches);
                      }}
                    />
                  </Col>
                  <Col span={10}>
                    <Input
                      placeholder="客队名称"
                      value={match.awayTeam}
                      onChange={(e) => {
                        const newMatches = [...batchMatches];
                        newMatches[index].awayTeam = e.target.value;
                        setBatchMatches(newMatches);
                      }}
                    />
                  </Col>
                  <Col span={4}>
                    <Button danger size="small" onClick={() => {
                      const newMatches = batchMatches.filter((_, i) => i !== index);
                      setBatchMatches(newMatches);
                    }}>删除</Button>
                  </Col>
                </Row>
              ))
            )}
            <Button type="dashed" block icon={<ClusterOutlined />} onClick={addBatchMatch} style={{ marginTop: 8 }}>添加更多比赛</Button>
          </div>
        )}
      </Modal>

      {loading && (
        <Card style={{ textAlign: 'center', marginBottom: 24, padding: 48 }}>
          <Spin size="large" tip="正在计算预测..." />
        </Card>
      )}

      {error && (
        <Card style={{ marginBottom: 24 }}>
          <Alert 
            type="error"
            message="预测失败"
            description={error}
            style={{ marginBottom: 16 }}
          />
          <Button type="primary" onClick={handlePredict}>重新尝试</Button>
        </Card>
      )}

      {prediction && (
        <div>
          <Alert 
            type="info"
            message={`进攻效率对比: ${prediction.match.homeTeam.name} Lambda=${prediction.lambda.home.toFixed(3)} vs ${prediction.match.awayTeam.name} Lambda=${prediction.lambda.away.toFixed(3)} (比值: ${prediction.lambda.ratio.toFixed(2)})`}
            style={{ marginBottom: 24 }}
          />

          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={8}>
              <Card hoverable>
                <Statistic 
                  title="主胜概率" 
                  value={(prediction.predictions.winDrawLose.win * 100).toFixed(1)}
                  suffix="%"
                  valueStyle={{ color: '#10b981', fontSize: 32 }}
                  prefix={<TrophyOutlined />}
                />
              </Card>
            </Col>
            <Col span={8}>
              <Card hoverable>
                <Statistic 
                  title="平局概率" 
                  value={(prediction.predictions.winDrawLose.draw * 100).toFixed(1)}
                  suffix="%"
                  valueStyle={{ color: '#f59e0b', fontSize: 32 }}
                />
              </Card>
            </Col>
            <Col span={8}>
              <Card hoverable>
                <Statistic 
                  title="客胜概率" 
                  value={(prediction.predictions.winDrawLose.lose * 100).toFixed(1)}
                  suffix="%"
                  valueStyle={{ color: '#ef4444', fontSize: 32 }}
                />
              </Card>
            </Col>
          </Row>

          <Tabs defaultActiveKey="1" size="large" style={{ marginBottom: 24 }} items={[
            { key: '1', label: '胜平负', children: <ReactECharts option={winDrawLoseChartOption} style={{ height: 380 }} opts={{ renderer: 'svg' }} /> },
            { key: '2', label: '比分分布', children: <ReactECharts option={scoreChartOption} style={{ height: 380 }} opts={{ renderer: 'svg' }} /> },
            { key: '3', label: '总进球', children: <ReactECharts option={totalGoalsChartOption} style={{ height: 380 }} opts={{ renderer: 'svg' }} /> },
            { key: '4', label: 'Lambda对比', children: <ReactECharts option={lambdaChartOption} style={{ height: 380 }} opts={{ renderer: 'svg' }} /> },
            { key: '5', label: <span><ClusterOutlined /> 模型分解</span>, children: prediction?.ensemble?.components ? (
              <div>
                <Row gutter={16} style={{ marginBottom: 16 }}>
                  <Col span={12}>
                    <ReactECharts option={modelBreakdownChartOption} style={{ height: 350 }} opts={{ renderer: 'svg' }} />
                  </Col>
                  <Col span={12}>
                    <ReactECharts option={ensembleWeightsChartOption} style={{ height: 350 }} opts={{ renderer: 'svg' }} />
                  </Col>
                </Row>
                <Card title="6模型独立预测详情" size="small">
                  <Table 
                    dataSource={modelBreakdownData} 
                    columns={modelBreakdownColumns}
                    pagination={false}
                    size="small"
                    rowKey="key"
                  />
                </Card>
                {prediction.ensemble.matchType && (
                  <Alert 
                    type="info" 
                    message={`比赛类型: ${prediction.ensemble.matchType} | 集成模型: ${prediction.ensemble.model || 'Stacked-Ensemble'}`}
                    style={{ marginTop: 16 }}
                  />
                )}
              </div>
            ) : <Alert type="warning" message="模型分解数据不可用，请检查后端是否返回 ensemble.components" /> },
            { key: '6', label: <span><SafetyOutlined /> 置信度</span>, children: prediction?.confidence != null ? (
              <div>
                <Row gutter={16}>
                  <Col span={8}>
                    <Card hoverable>
                      <Statistic 
                        title="模型置信度" 
                        value={(prediction.confidence * 100).toFixed(1)}
                        suffix="%"
                        valueStyle={{ color: prediction.confidence > 0.6 ? '#10b981' : prediction.confidence > 0.4 ? '#f59e0b' : '#ef4444', fontSize: 32 }}
                        prefix={<SafetyOutlined />}
                      />
                    </Card>
                  </Col>
                  <Col span={8}>
                    <Card hoverable>
                      <Statistic 
                        title="蒙特卡洛不确定性" 
                        value={prediction.uncertainty ? (prediction.uncertainty.stddev * 100).toFixed(2) : 'N/A'}
                        suffix="%"
                        valueStyle={{ color: '#8b5cf6', fontSize: 32 }}
                      />
                    </Card>
                  </Col>
                  <Col span={8}>
                    <Card hoverable>
                      <Statistic 
                        title="Elo评分差" 
                        value={prediction.teams?.home?.elo && prediction.teams?.away?.elo 
                          ? Math.round(prediction.teams.home.elo - prediction.teams.away.elo) 
                          : 'N/A'}
                        suffix=""
                        valueStyle={{ color: '#3b82f6', fontSize: 32 }}
                        prefix={<DashboardOutlined />}
                      />
                    </Card>
                  </Col>
                </Row>
                {prediction.uncertainty && (
                  <Card title="蒙特卡洛模拟详情" style={{ marginTop: 16 }}>
                    <Row gutter={16}>
                      <Col span={6}><Statistic title="模拟次数" value={prediction.uncertainty.simulations || 500} /></Col>
                      <Col span={6}><Statistic title="主胜均值" value={`${(prediction.uncertainty.meanWinA * 100).toFixed(1)}%`} /></Col>
                      <Col span={6}><Statistic title="标准差" value={`${(prediction.uncertainty.stddev * 100).toFixed(2)}%`} /></Col>
                      <Col span={6}><Statistic title="95%置信区间" value={prediction.uncertainty.ci95 ? `[${(prediction.uncertainty.ci95[0] * 100).toFixed(1)}%, ${(prediction.uncertainty.ci95[1] * 100).toFixed(1)}%]` : 'N/A'} /></Col>
                    </Row>
                  </Card>
                )}
                {prediction.teams?.home?.elo && prediction.teams?.away?.elo && (
                  <Card title="Elo评分对比" style={{ marginTop: 16 }}>
                    <Row gutter={16}>
                      <Col span={12}>
                        <Statistic 
                          title={`${prediction.match.homeTeam.name} Elo`} 
                          value={Math.round(prediction.teams.home.elo)}
                          valueStyle={{ color: '#10b981' }}
                        />
                      </Col>
                      <Col span={12}>
                        <Statistic 
                          title={`${prediction.match.awayTeam.name} Elo`} 
                          value={Math.round(prediction.teams.away.elo)}
                          valueStyle={{ color: '#ef4444' }}
                        />
                      </Col>
                    </Row>
                  </Card>
                )}
              </div>
            ) : <Alert type="warning" message="置信度数据不可用" /> },
          ]} />

          <Card title="比分概率排名">
            <Table 
              dataSource={prediction.predictions.topScores.slice(0, 10)} 
              columns={scoreColumns}
              pagination={false}
              rowKey="score"
            />
          </Card>

          <Row gutter={16} style={{ marginTop: 24 }}>
            <Col span={12}>
              <Card title="让球盘(-1)">
                <Row gutter={8}>
                  <Col span={8}>
                    <Statistic 
                      title="胜(净2+)" 
                      value={(prediction.predictions.handicap.win * 100).toFixed(1)}
                      suffix="%"
                      valueStyle={{ color: '#10b981' }}
                    />
                  </Col>
                  <Col span={8}>
                    <Statistic 
                      title="平(净1)" 
                      value={(prediction.predictions.handicap.draw * 100).toFixed(1)}
                      suffix="%"
                      valueStyle={{ color: '#f59e0b' }}
                    />
                  </Col>
                  <Col span={8}>
                    <Statistic 
                      title="负" 
                      value={(prediction.predictions.handicap.lose * 100).toFixed(1)}
                      suffix="%"
                      valueStyle={{ color: '#ef4444' }}
                    />
                  </Col>
                </Row>
              </Card>
            </Col>
            <Col span={12}>
              <Card title="大小球(2.5)">
                <Row gutter={8}>
                  <Col span={12}>
                    <Statistic 
                      title="大2.5" 
                      value={(prediction.predictions.totalGoals.over25 * 100).toFixed(1)}
                      suffix="%"
                      valueStyle={{ color: '#10b981' }}
                    />
                  </Col>
                  <Col span={12}>
                    <Statistic 
                      title="小2.5" 
                      value={(prediction.predictions.totalGoals.under25 * 100).toFixed(1)}
                      suffix="%"
                      valueStyle={{ color: '#3b82f6' }}
                    />
                  </Col>
                </Row>
              </Card>
            </Col>
          </Row>
        </div>
      )}
    </div>
  );
}

export default Predict;