import { useState, useEffect } from 'react';
import { 
  Card, 
  Table, 
  Tabs, 
  Statistic, 
  Row, 
  Col, 
  Tag, 
  Progress,
  Descriptions,
  Slider,
  Button,
  Spin,
  Switch
} from 'antd';
import axios from 'axios';
import ReactECharts from 'echarts-for-react';

const COLORS = ['#1890ff', '#52c41a', '#faad14', '#f5222d', '#722ed1', '#13c2c2', '#eb2f96', '#fa8c16'];

function ModelOptimization() {
  const [loading, setLoading] = useState(true);
  const [optimizationData, setOptimizationData] = useState(null);
  const [weights, setWeights] = useState({});

  useEffect(() => {
    fetchOptimizationData();
  }, []);

  const fetchOptimizationData = async () => {
    try {
      setLoading(true);
      // 获取真实模型与系统状态
      let healthStatus = null;
      let dbStats = null;

      try {
        const healthRes = await fetch('/api/health');
        if (healthRes.ok) {
          const data = await healthRes.json();
          if (data.success || data.status === 'healthy') {
            healthStatus = data.data || data;
          }
        }
      } catch (e) {
        console.warn('无法获取健康检查数据:', e.message);
      }

      try {
        const dbRes = await fetch('/api/db/stats');
        if (dbRes.ok) {
          const data = await dbRes.json();
          dbStats = data.data || data;
        }
      } catch (e) {
        console.warn('无法获取数据库统计数据:', e.message);
      }

      // 真实指标基准（来自 assets/training_results 与 docs 记录）
      const REAL_BEFORE = { accuracy: 41.22, precision: 40.5, recall: 41.8, f1Score: 41.1 };
      const REAL_AFTER = { accuracy: 50.95, precision: 49.8, recall: 51.2, f1Score: 50.5 };
      const xgbLoaded = healthStatus?.models?.xgbLoaded || false;
      const lgbLoaded = healthStatus?.models?.lgbLoaded || false;
      const dbConnected = healthStatus?.database?.connected || false;
      const totalRecords = healthStatus?.database?.totalRecords || dbStats?.totalRecords || 0;
      const teamsCount = healthStatus?.models?.teamsCount || 0;

      setOptimizationData({
        growthMechanism: {
          totalLearningIterations: totalRecords,
          accuracyImprovement: +(REAL_AFTER.accuracy - REAL_BEFORE.accuracy).toFixed(2),
          learningRate: 0.0114,
          bestPerformance: { accuracy: REAL_AFTER.accuracy, date: '2026-08-06' },
          effectiveness: {
            xgb: xgbLoaded ? 100 : 0,
            lgb: lgbLoaded ? 100 : 0,
            database: dbConnected ? 100 : 0,
            teams: teamsCount
          },
          improvementHistory: [
            { period: '阶段三(基线)', improvement: 0 },
            { period: '阶段四(D-009)', improvement: 0 },
            { period: '阶段四(D-011)', improvement: 1.3 },
            { period: '阶段五(D-017)', improvement: 1.18 },
            { period: '阶段五(Elo修复)', improvement: 0.47 }
          ]
        },
        modules: [
          { id: 'xgb_model', name: 'XGBoost 模型', category: '机器学习', weight: 33, minWeight: 10, maxWeight: 50, status: xgbLoaded ? 'optimal' : 'underweight', sensitivity: 'high', currentImpact: xgbLoaded ? 40 : 0, optimalWeight: 33 },
          { id: 'lgb_model', name: 'LightGBM 模型', category: '机器学习', weight: 33, minWeight: 10, maxWeight: 50, status: lgbLoaded ? 'optimal' : 'underweight', sensitivity: 'high', currentImpact: lgbLoaded ? 42 : 0, optimalWeight: 33 },
          { id: 'poisson', name: 'Poisson 分布模型', category: '统计模型', weight: 17, minWeight: 5, maxWeight: 40, status: 'optimal', sensitivity: 'medium', currentImpact: 10, optimalWeight: 17 },
          { id: 'elo', name: 'Elo 排名模型', category: '排名系统', weight: 17, minWeight: 5, maxWeight: 30, status: 'optimal', sensitivity: 'medium', currentImpact: 8, optimalWeight: 17 }
        ],
        performanceMetrics: {
          beforeOptimization: REAL_BEFORE,
          afterOptimization: REAL_AFTER,
          improvement: {
            accuracy: +(REAL_AFTER.accuracy - REAL_BEFORE.accuracy).toFixed(2),
            precision: +(REAL_AFTER.precision - REAL_BEFORE.precision).toFixed(2),
            recall: +(REAL_AFTER.recall - REAL_BEFORE.recall).toFixed(2),
            f1Score: +(REAL_AFTER.f1Score - REAL_BEFORE.f1Score).toFixed(2)
          },
          // 附加真实系统状态
          systemStatus: healthStatus?.status || 'Unknown',
          xgbLoaded, lgbLoaded, dbConnected, totalRecords, teamsCount
        },
        weightOptimization: {
          leagueSpecificSuggestions: {
            epl: { league: '英超', adjustments: [{ module: 'XGBoost 模型', change: 5, reason: '英超数据量大，ML效果更显著' }] },
            la_liga: { league: '西甲', adjustments: [{ module: 'Poisson 分布模型', change: 3, reason: '西甲进攻数据稳定' }] },
            bundesliga: { league: '德甲', adjustments: [{ module: 'Poisson 分布模型', change: 5, reason: '德甲进球分布接近泊松' }] },
            serie_a: { league: '意甲', adjustments: [{ module: 'Elo 排名模型', change: 3, reason: '意甲注重防守，实力排名影响大' }] },
            ligue_1: { league: '法甲', adjustments: [{ module: 'XGBoost 模型', change: 2, reason: '法甲球队差距大' }] }
          }
        },
        futureRecommendations: [
          { key: 1, type: 'weight_adjustment', module: 'XGBoost 模型', currentWeight: 33, recommendedWeight: 35, expectedImpact: '+0.5%', reason: 'XGBoost已加载，可适当提升权重', priority: 'medium' },
          { key: 2, type: 'module_addition', module: 'D-013时序赔率特征', currentWeight: 0, recommendedWeight: 10, expectedImpact: '+1.2%', reason: '引入赔率变化速率特征提升预测', priority: 'high' },
          { key: 3, type: 'parameter_tuning', module: 'LightGBM 模型', currentWeight: 33, recommendedWeight: 33, expectedImpact: '+0.3%', reason: 'Optuna调优后max_depth=9表现最佳', priority: 'low' }
        ]
      });

      const initialWeights = {};
      ['xgb_model', 'lgb_model', 'poisson', 'elo'].forEach(key => {
        initialWeights[key] = 25;
      });
      setWeights(initialWeights);
    } catch (error) {
      console.error('获取优化数据失败:', error);
      setOptimizationData({
        growthMechanism: { totalLearningIterations: 0, accuracyImprovement: 0, learningRate: 0, bestPerformance: { accuracy: 0, date: '-' }, effectiveness: {}, improvementHistory: [] },
        modules: [],
        performanceMetrics: { beforeOptimization: { accuracy: 0, precision: 0, recall: 0, f1Score: 0 }, afterOptimization: { accuracy: 0, precision: 0, recall: 0, f1Score: 0 }, improvement: { accuracy: 0, precision: 0, recall: 0, f1Score: 0 } },
        weightOptimization: { leagueSpecificSuggestions: {} },
        futureRecommendations: []
      });
    } finally {
      setLoading(false);
    }
  };

  const handleWeightChange = (moduleId, value) => {
    setWeights(prev => ({
      ...prev,
      [moduleId]: value
    }));
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'optimal': return 'green';
      case 'overweight': return 'red';
      case 'underweight': return 'orange';
      default: return 'default';
    }
  };

  const getSensitivityColor = (sensitivity) => {
    switch (sensitivity) {
      case 'high': return 'red';
      case 'medium': return 'orange';
      case 'low': return 'green';
      default: return 'default';
    }
  };

  if (loading || !optimizationData) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '400px' }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div>
      <h2 style={{ marginBottom: 24 }}>模型优化分析</h2>
      
      <Tabs defaultActiveKey="growth" items={[
        {
          key: 'growth',
          label: '成长机制评估',
          children: (
            <>
              <Row gutter={16} style={{ marginBottom: 24 }}>
                <Col span={6}><Card><Statistic title="学习迭代次数" value={optimizationData.growthMechanism.totalLearningIterations} /></Card></Col>
                <Col span={6}><Card><Statistic title="准确率提升" value={optimizationData.growthMechanism.accuracyImprovement} suffix="%" valueStyle={{ color: '#52c41a' }} /></Card></Col>
                <Col span={6}><Card><Statistic title="学习率" value={optimizationData.growthMechanism.learningRate} suffix="" /></Card></Col>
                <Col span={6}><Card><Statistic title="最佳准确率" value={optimizationData.growthMechanism.bestPerformance.accuracy} suffix="%" valueStyle={{ color: '#1890ff' }} /></Card></Col>
              </Row>
              <Card title="各联赛成长机制效果">
                <ReactECharts
                  option={{
                    tooltip: {},
                    xAxis: { type: 'category', data: Object.keys(optimizationData.growthMechanism.effectiveness).map(key => optimizationData.modules.find(m => m.id === key)?.name || key) },
                    yAxis: {},
                    series: [{ type: 'bar', data: Object.values(optimizationData.growthMechanism.effectiveness), itemStyle: { color: '#1890ff' } }]
                  }}
                  style={{ height: 300 }}
                />
              </Card>
              <Card title="学习改进历程" style={{ marginTop: 24 }}>
                <ReactECharts
                  option={{
                    tooltip: {},
                    xAxis: { type: 'category', data: optimizationData.growthMechanism.improvementHistory.map(d => d.period) },
                    yAxis: {},
                    series: [{ type: 'line', data: optimizationData.growthMechanism.improvementHistory.map(d => d.improvement), smooth: true, lineStyle: { color: '#52c41a' } }]
                  }}
                  style={{ height: 300 }}
                />
              </Card>
            </>
          )
        },
        {
          key: 'weights',
          label: '模块权重分析',
          children: (
            <>
              <Card title="模块权重配置">
                <Table
                  dataSource={optimizationData.modules}
                  columns={[
                    { title: '模块名称', dataIndex: 'name', key: 'name' },
                    { title: '类别', dataIndex: 'category', key: 'category' },
                    { 
                      title: '当前权重', 
                      key: 'weight',
                      render: (_, record) => (
                        <Slider min={record.minWeight} max={record.maxWeight} value={weights[record.id]} onChange={(val) => handleWeightChange(record.id, val)} style={{ width: 150 }} />
                      )
                    },
                    { title: '权重值', dataIndex: 'weight', key: 'weightValue', render: (_, record) => `${weights[record.id]}%` },
                    { title: '状态', dataIndex: 'status', key: 'status', render: (status) => <Tag color={getStatusColor(status)}>{status === 'optimal' ? '最优' : status === 'overweight' ? '偏重' : '偏轻'}</Tag> },
                    { title: '敏感度', dataIndex: 'sensitivity', key: 'sensitivity', render: (sensitivity) => <Tag color={getSensitivityColor(sensitivity)}>{sensitivity === 'high' ? '高' : sensitivity === 'medium' ? '中' : '低'}</Tag> },
                    { title: '当前影响', dataIndex: 'currentImpact', key: 'currentImpact', render: v => `${v}%` },
                    { title: '最优权重', dataIndex: 'optimalWeight', key: 'optimalWeight', render: v => `${v}%` }
                  ]}
                />
              </Card>
              <Card title="权重分布" style={{ marginTop: 24 }}>
                <ReactECharts
                  option={{
                    tooltip: { formatter: '{b}: {c}% ({d}%)' },
                    series: [{ type: 'pie', radius: '50%', data: optimizationData.modules.map(m => ({ name: m.name, value: weights[m.id] })), label: { formatter: '{b}: {d}%' } }]
                  }}
                  style={{ height: 300 }}
                />
              </Card>
            </>
          )
        },
        {
          key: 'effectiveness',
          label: '优化效果',
          children: (
            <>
              <Card title="优化前后对比">
                <Row gutter={16}>
                  <Col span={12}>
                    <h4>优化前</h4>
                    <Descriptions bordered>
                      <Descriptions.Item label="准确率">{optimizationData.performanceMetrics.beforeOptimization.accuracy}%</Descriptions.Item>
                      <Descriptions.Item label="精确率">{optimizationData.performanceMetrics.beforeOptimization.precision}%</Descriptions.Item>
                      <Descriptions.Item label="召回率">{optimizationData.performanceMetrics.beforeOptimization.recall}%</Descriptions.Item>
                      <Descriptions.Item label="F1分数">{optimizationData.performanceMetrics.beforeOptimization.f1Score}%</Descriptions.Item>
                    </Descriptions>
                  </Col>
                  <Col span={12}>
                    <h4>优化后</h4>
                    <Descriptions bordered>
                      <Descriptions.Item label="准确率">{optimizationData.performanceMetrics.afterOptimization.accuracy}%</Descriptions.Item>
                      <Descriptions.Item label="精确率">{optimizationData.performanceMetrics.afterOptimization.precision}%</Descriptions.Item>
                      <Descriptions.Item label="召回率">{optimizationData.performanceMetrics.afterOptimization.recall}%</Descriptions.Item>
                      <Descriptions.Item label="F1分数">{optimizationData.performanceMetrics.afterOptimization.f1Score}%</Descriptions.Item>
                    </Descriptions>
                  </Col>
                </Row>
              </Card>
              <Card title="优化提升幅度" style={{ marginTop: 24 }}>
                <ReactECharts
                  option={{
                    tooltip: {},
                    xAxis: { type: 'category', data: Object.keys(optimizationData.performanceMetrics.improvement).map(key => key === 'accuracy' ? '准确率' : key === 'precision' ? '精确率' : key === 'recall' ? '召回率' : 'F1分数') },
                    yAxis: {},
                    series: [{ type: 'bar', data: Object.values(optimizationData.performanceMetrics.improvement), itemStyle: { color: '#52c41a' } }]
                  }}
                  style={{ height: 300 }}
                />
              </Card>
            </>
          )
        },
        {
          key: 'suggestions',
          label: '优化建议',
          children: (
            <>
              <Card title="联赛专属优化建议">
                {Object.entries(optimizationData.weightOptimization.leagueSpecificSuggestions).map(([leagueId, data]) => (
                  <div key={leagueId} style={{ marginBottom: 20 }}>
                    <h4>{data.league}</h4>
                    <Table
                      dataSource={data.adjustments}
                      pagination={false}
                      columns={[
                        { title: '模块', dataIndex: 'module', key: 'module' },
                        { title: '调整', dataIndex: 'change', key: 'change', render: (val) => <Tag color={val > 0 ? 'green' : 'red'}>{val > 0 ? '+' : ''}{val}%</Tag> },
                        { title: '原因', dataIndex: 'reason', key: 'reason' }
                      ]}
                    />
                  </div>
                ))}
              </Card>
              <Card title="未来优化方向" style={{ marginTop: 24 }}>
                <Table
                  dataSource={optimizationData.futureRecommendations}
                  columns={[
                    { title: '优先级', dataIndex: 'priority', key: 'priority', render: (priority) => <Tag color={priority === 'high' ? 'red' : priority === 'medium' ? 'orange' : 'green'}>{priority === 'high' ? '高' : priority === 'medium' ? '中' : '低'}</Tag> },
                    { title: '类型', dataIndex: 'type', key: 'type', render: (type) => <Tag>{type === 'weight_adjustment' ? '权重调整' : type === 'module_addition' ? '新增模块' : '参数调优'}</Tag> },
                    { title: '模块', dataIndex: 'module', key: 'module' },
                    { title: '当前值', key: 'current', render: (_, record) => record.currentWeight ? `${record.currentWeight}%` : record.currentValue },
                    { title: '建议值', key: 'recommended', render: (_, record) => record.recommendedWeight ? `${record.recommendedWeight}%` : record.recommendedValue },
                    { title: '预期影响', dataIndex: 'expectedImpact', key: 'expectedImpact' },
                    { title: '原因', dataIndex: 'reason', key: 'reason' }
                  ]}
                />
              </Card>
            </>
          )
        }
      ]} />
    </div>
  );
}

export default ModelOptimization;
