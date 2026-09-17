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
  Spin
} from 'antd';
import axios from 'axios';
import ReactECharts from 'echarts-for-react';

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];

const PredictionEvaluation = () => {
  const [evaluationData, setEvaluationData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchEvaluationData();
  }, []);

  const fetchEvaluationData = async () => {
    setLoading(true);
    try {
      const res = await axios.get('/api/predict/evaluation');
      setEvaluationData(res.data.data);
    } catch (error) {
      console.error('获取评估数据失败:', error);
      setEvaluationData(getMockEvaluationData());
    } finally {
      setLoading(false);
    }
  };

  const getMockEvaluationData = () => {
    return {
      overall: {
        accuracy: 78.3,
        precision: 76.5,
        recall: 77.2,
        f1Score: 76.8,
        totalPredictions: 1560,
        correctPredictions: 1221,
        leagues: 5
      },
      byLeague: [
        {
          leagueId: 'premier_league',
          leagueName: '英超',
          accuracy: 81.2,
          precision: 79.5,
          recall: 80.3,
          f1Score: 79.9,
          totalPredictions: 380,
          correctPredictions: 309,
          winRate: 82.5,
          drawRate: 75.8,
          lossRate: 81.3,
          underOverRate: 78.6,
          handicapRate: 76.2,
          strengths: ['攻防节奏快', '数据积累丰富', 'VAR覆盖率高'],
          weaknesses: ['平局预测偏低', '伤病影响大'],
          rank: 1
        },
        {
          leagueId: 'la_liga',
          leagueName: '西甲',
          accuracy: 79.8,
          precision: 78.2,
          recall: 78.9,
          f1Score: 78.5,
          totalPredictions: 380,
          correctPredictions: 303,
          winRate: 80.2,
          drawRate: 76.5,
          lossRate: 79.7,
          underOverRate: 80.1,
          handicapRate: 77.5,
          strengths: ['技术流为主', '控球数据完整'],
          weaknesses: ['冬季赛程密集', '冷门较多'],
          rank: 2
        },
        {
          leagueId: 'serie_a',
          leagueName: '意甲',
          accuracy: 77.5,
          precision: 76.1,
          recall: 76.8,
          f1Score: 76.4,
          totalPredictions: 380,
          correctPredictions: 295,
          winRate: 78.3,
          drawRate: 75.2,
          lossRate: 77.1,
          underOverRate: 76.8,
          handicapRate: 75.9,
          strengths: ['防守数据详实'],
          weaknesses: ['节奏较慢', '平局较多'],
          rank: 3
        },
        {
          leagueId: 'bundesliga',
          leagueName: '德甲',
          accuracy: 76.2,
          precision: 75.3,
          recall: 75.8,
          f1Score: 75.5,
          totalPredictions: 306,
          correctPredictions: 233,
          winRate: 77.5,
          drawRate: 74.1,
          lossRate: 76.8,
          underOverRate: 78.2,
          handicapRate: 74.5,
          strengths: ['进攻效率高', '数据透明度好'],
          weaknesses: ['强队差距大', '冬歇期较长'],
          rank: 4
        },
        {
          leagueId: 'ligue_1',
          leagueName: '法甲',
          accuracy: 75.1,
          precision: 74.2,
          recall: 74.8,
          f1Score: 74.5,
          totalPredictions: 380,
          correctPredictions: 285,
          winRate: 76.8,
          drawRate: 73.5,
          lossRate: 75.2,
          underOverRate: 75.6,
          handicapRate: 73.8,
          strengths: ['巴黎统治力强'],
          weaknesses: ['联赛竞争力不均', '数据质量待提升'],
          rank: 5
        }
      ],
      byMetric: {
        matchResult: {
          overall: 78.3,
          breakdown: {
            homeWin: 81.5,
            draw: 72.3,
            awayWin: 77.1
          }
        },
        overUnder: {
          overall: 77.8,
          breakdown: {
            over25: 78.2,
            under25: 77.4
          }
        },
        handicap: {
          overall: 75.6,
          breakdown: {
            homeHandicap: 76.1,
            awayHandicap: 75.1
          }
        },
        scoreRange: {
          overall: 68.5,
          breakdown: {
            '0-0': 72.1,
            '1-0': 68.3,
            '1-1': 70.5,
            '2-0': 66.8,
            '2-1': 67.2,
            other: 65.1
          }
        }
      },
      trends: [
        { date: '2024-08', accuracy: 75.2 },
        { date: '2024-09', accuracy: 76.8 },
        { date: '2024-10', accuracy: 77.5 },
        { date: '2024-11', accuracy: 78.1 },
        { date: '2024-12', accuracy: 77.8 },
        { date: '2025-01', accuracy: 78.5 },
        { date: '2025-02', accuracy: 79.2 },
        { date: '2025-03', accuracy: 78.8 },
        { date: '2025-04', accuracy: 79.5 },
        { date: '2025-05', accuracy: 78.3 }
      ],
      suggestions: [
        {
          id: 1,
          category: '模型优化',
          title: '加强平局预测能力',
          description: '当前平局预测准确率(72.3%)低于胜场预测，建议增加平局概率修正因子',
          priority: 'high',
          impact: '预计提升2-3%整体准确率'
        },
        {
          id: 2,
          category: '数据增强',
          title: '引入法甲更多数据源',
          description: '法甲数据质量相对较低，建议接入更多专业数据提供商',
          priority: 'high',
          impact: '预计提升法甲预测1-2%'
        },
        {
          id: 3,
          category: '特征工程',
          title: '增加冬歇期状态评估',
          description: '德甲和意甲冬歇期较长，建议增加冬歇期前后状态变化模型',
          priority: 'medium',
          impact: '预计提升冬季赛事预测1-1.5%'
        },
        {
          id: 4,
          category: '模型优化',
          title: '优化让球盘预测算法',
          description: '让球盘预测准确率(75.6%)低于胜平负预测，建议引入更精准的让球计算逻辑',
          priority: 'medium',
          impact: '预计提升1-2%'
        },
        {
          id: 5,
          category: '数据增强',
          title: '增加天气影响因子',
          description: '德甲受天气影响较大，建议增加天气数据作为预测特征',
          priority: 'low',
          impact: '预计提升0.5-1%'
        }
      ]
    };
  };

  const getPriorityColor = (priority) => {
    switch (priority) {
      case 'high': return 'red';
      case 'medium': return 'orange';
      case 'low': return 'green';
      default: return 'default';
    }
  };

  if (loading || !evaluationData) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '400px' }}>
        <Spin size="large" />
      </div>
    );
  }

  const radarChartOption = {
    title: { text: '五大联赛综合能力对比', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'item', textStyle: { color: '#f1f5f9' } },
    legend: { data: evaluationData.byLeague.map(l => l.leagueName), bottom: '5%', textStyle: { color: '#94a3b8', fontSize: 12 } },
    radar: {
      indicator: [
        { name: '准确率', max: 100 },
        { name: '精确率', max: 100 },
        { name: '召回率', max: 100 },
        { name: 'F1分数', max: 100 },
        { name: '大小球', max: 100 },
        { name: '让球盘', max: 100 }
      ],
      axisName: { color: '#94a3b8', fontSize: 12 },
      splitArea: { areaStyle: { color: ['rgba(255,255,255,0.05)', 'rgba(255,255,255,0.02)'] } },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    series: [{
      type: 'radar',
      data: evaluationData.byLeague.map((item, index) => ({
        name: item.leagueName,
        value: [item.accuracy, item.precision, item.recall, item.f1Score, item.underOverRate, item.handicapRate],
        lineStyle: { color: COLORS[index], width: 2 },
        areaStyle: { color: COLORS[index], opacity: 0.2 },
        itemStyle: { color: COLORS[index] }
      }))
    }]
  };

  const winDrawLoseChartOption = {
    title: { text: '胜平负预测分析', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { color: '#f1f5f9' } },
    legend: { data: ['胜场预测率', '平局预测率', '负场预测率'], bottom: '5%', textStyle: { color: '#94a3b8', fontSize: 12 } },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '10%', containLabel: true },
    xAxis: { type: 'category', data: evaluationData.byLeague.map(l => l.leagueName), axisLabel: { color: '#94a3b8', fontSize: 12 }, axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } } },
    yAxis: { type: 'value', axisLabel: { color: '#94a3b8', fontSize: 12 }, axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } } },
    series: [
      { name: '胜场预测率', type: 'bar', data: evaluationData.byLeague.map(l => l.winRate), itemStyle: { color: '#10b981' } },
      { name: '平局预测率', type: 'bar', data: evaluationData.byLeague.map(l => l.drawRate), itemStyle: { color: '#f59e0b' } },
      { name: '负场预测率', type: 'bar', data: evaluationData.byLeague.map(l => l.lossRate), itemStyle: { color: '#ef4444' } }
    ]
  };

  const overUnderHandicapChartOption = {
    title: { text: '大小球与让球盘预测', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { color: '#f1f5f9' } },
    legend: { data: ['大小球预测率', '让球盘预测率'], bottom: '5%', textStyle: { color: '#94a3b8', fontSize: 12 } },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '10%', containLabel: true },
    xAxis: { type: 'category', data: evaluationData.byLeague.map(l => l.leagueName), axisLabel: { color: '#94a3b8', fontSize: 12 }, axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } } },
    yAxis: { type: 'value', axisLabel: { color: '#94a3b8', fontSize: 12 }, axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } } },
    series: [
      { name: '大小球预测率', type: 'bar', data: evaluationData.byLeague.map(l => l.underOverRate), itemStyle: { color: '#3b82f6' } },
      { name: '让球盘预测率', type: 'bar', data: evaluationData.byLeague.map(l => l.handicapRate), itemStyle: { color: '#8b5cf6' } }
    ]
  };

  const trendChartOption = {
    title: { text: '预测准确率趋势', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '10%', containLabel: true },
    xAxis: { type: 'category', data: evaluationData.trends.map(t => t.date), axisLabel: { color: '#94a3b8', fontSize: 12 }, axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } } },
    yAxis: { type: 'value', axisLabel: { color: '#94a3b8', fontSize: 12 }, axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } } },
    series: [{
      type: 'line',
      data: evaluationData.trends.map(t => t.accuracy),
      smooth: true,
      lineStyle: { width: 3, color: '#3b82f6' },
      areaStyle: { color: '#3b82f6', opacity: 0.2 },
      symbol: 'circle',
      symbolSize: 8,
      itemStyle: { color: '#3b82f6' }
    }]
  };

  const pieChartOption = {
    title: { text: '预测类型分布', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'item', textStyle: { color: '#f1f5f9' }, formatter: '{b}: {c}% ({d}%)' },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 8, borderColor: '#1e293b', borderWidth: 2 },
      label: { show: true, color: '#94a3b8', formatter: '{b}: {c}%' },
      data: [
        { value: evaluationData.byMetric.matchResult.overall, name: '胜平负', itemStyle: { color: '#3b82f6' } },
        { value: evaluationData.byMetric.overUnder.overall, name: '大小球', itemStyle: { color: '#10b981' } },
        { value: evaluationData.byMetric.handicap.overall, name: '让球盘', itemStyle: { color: '#8b5cf6' } },
        { value: evaluationData.byMetric.scoreRange.overall, name: '比分范围', itemStyle: { color: '#f59e0b' } }
      ]
    }]
  };

  return (
    <div className="fade-in">
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ margin: 0, background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>五大联赛预测能力评估</h2>
      </div>
      
      <Tabs defaultActiveKey="overview" size="large" items={[
        {
          key: 'overview',
          label: '评估概览',
          children: (
            <>
              <Row gutter={16} style={{ marginBottom: 24 }}>
                <Col span={6}>
                  <Card hoverable>
                    <Statistic 
                      title="总体准确率" 
                      value={evaluationData.overall.accuracy} 
                      suffix="%"
                      valueStyle={{ fontSize: 28, color: '#3b82f6' }}
                    />
                  </Card>
                </Col>
                <Col span={6}>
                  <Card hoverable>
                    <Statistic 
                      title="精确率" 
                      value={evaluationData.overall.precision} 
                      suffix="%"
                      valueStyle={{ fontSize: 28, color: '#10b981' }}
                    />
                  </Card>
                </Col>
                <Col span={6}>
                  <Card hoverable>
                    <Statistic 
                      title="召回率" 
                      value={evaluationData.overall.recall} 
                      suffix="%"
                      valueStyle={{ fontSize: 28, color: '#f59e0b' }}
                    />
                  </Card>
                </Col>
                <Col span={6}>
                  <Card hoverable>
                    <Statistic 
                      title="F1分数" 
                      value={evaluationData.overall.f1Score} 
                      suffix="%"
                      valueStyle={{ fontSize: 28, color: '#8b5cf6' }}
                    />
                  </Card>
                </Col>
              </Row>
              <Card title="各联赛预测表现排名">
                <Table
                  dataSource={evaluationData.byLeague}
                  columns={[
                    { 
                      title: '排名', 
                      key: 'rank',
                      render: (_, record) => (
                        <Tag color={record.rank <= 2 ? 'gold' : record.rank <= 3 ? 'default' : 'silver'}>
                          {record.rank}
                        </Tag>
                      )
                    },
                    { title: '联赛', dataIndex: 'leagueName', key: 'leagueName', render: (text) => <span style={{ color: '#f1f5f9' }}>{text}</span> },
                    { 
                      title: '准确率', 
                      dataIndex: 'accuracy', 
                      key: 'accuracy',
                      render: (acc) => (
                        <div>
                          <Progress percent={acc} size="small" strokeColor={COLORS[evaluationData.byLeague.findIndex(l => l.accuracy === acc)]} />
                          <span style={{ marginLeft: 8, color: '#94a3b8' }}>{acc}%</span>
                        </div>
                      )
                    },
                    { title: '总预测', dataIndex: 'totalPredictions', key: 'totalPredictions', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '正确预测', dataIndex: 'correctPredictions', key: 'correctPredictions', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> }
                  ]}
                />
              </Card>
            </>
          )
        },
        { key: 'radar', label: '能力雷达图', children: <Card title="五大联赛综合能力对比"><ReactECharts option={radarChartOption} style={{ height: 500 }} opts={{ renderer: 'svg' }} /></Card> },
        { 
          key: 'dimensions', 
          label: '维度分析', 
          children: (
            <>
              <Card title="胜平负预测分析"><ReactECharts option={winDrawLoseChartOption} style={{ height: 350 }} opts={{ renderer: 'svg' }} /></Card>
              <Card title="大小球与让球盘预测" style={{ marginTop: 24 }}><ReactECharts option={overUnderHandicapChartOption} style={{ height: 350 }} opts={{ renderer: 'svg' }} /></Card>
            </>
          )
        },
        { 
          key: 'trends', 
          label: '趋势分析', 
          children: (
            <>
              <Card title="预测准确率趋势"><ReactECharts option={trendChartOption} style={{ height: 400 }} opts={{ renderer: 'svg' }} /></Card>
              <Card title="预测类型分布" style={{ marginTop: 24 }}><ReactECharts option={pieChartOption} style={{ height: 300 }} opts={{ renderer: 'svg' }} /></Card>
            </>
          )
        },
        {
          key: 'strengths',
          label: '优劣势分析',
          children: (
            <Card title="各联赛优势与不足">
              <Table
                dataSource={evaluationData.byLeague}
                columns={[
                  { title: '联赛', dataIndex: 'leagueName', key: 'leagueName', render: (text) => <span style={{ color: '#f1f5f9' }}>{text}</span> },
                  { 
                    title: '优势', 
                    dataIndex: 'strengths', 
                    key: 'strengths',
                    render: (strengths) => (
                      <ul>
                        {strengths.map((s, i) => (
                          <li key={i}><Tag color="green">{s}</Tag></li>
                        ))}
                      </ul>
                    )
                  },
                  { 
                    title: '不足', 
                    dataIndex: 'weaknesses', 
                    key: 'weaknesses',
                    render: (weaknesses) => (
                      <ul>
                        {weaknesses.map((w, i) => (
                          <li key={i}><Tag color="red">{w}</Tag></li>
                        ))}
                      </ul>
                    )
                  },
                  { 
                    title: '建议', 
                    key: 'suggestion',
                    render: (_, record) => (
                      <Descriptions bordered column={1}>
                        <Descriptions.Item label="优化方向">
                          <span style={{ color: '#94a3b8' }}>
                            {record.rank <= 2 ? '保持优势，精细化调整' : 
                             record.rank <= 3 ? '学习领先联赛模型' : '加强数据采集与模型适配'}
                          </span>
                        </Descriptions.Item>
                      </Descriptions>
                    )
                  }
                ]}
              />
            </Card>
          )
        },
        {
          key: 'suggestions',
          label: '优化建议',
          children: (
            <Card title="针对性优化建议">
              <Table
                dataSource={evaluationData.suggestions}
                columns={[
                  { 
                    title: '优先级', 
                    dataIndex: 'priority', 
                    key: 'priority',
                    render: (priority) => (
                      <Tag color={getPriorityColor(priority)}>{priority === 'high' ? '高' : priority === 'medium' ? '中' : '低'}</Tag>
                    )
                  },
                  { title: '类别', dataIndex: 'category', key: 'category', render: (text) => <span style={{ color: '#f1f5f9' }}>{text}</span> },
                  { title: '标题', dataIndex: 'title', key: 'title', render: (text) => <span style={{ color: '#f1f5f9' }}>{text}</span> },
                  { title: '描述', dataIndex: 'description', key: 'description', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                  { title: '预期影响', dataIndex: 'impact', key: 'impact', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> }
                ]}
              />
            </Card>
          )
        }
      ]} />
    </div>
  );
};

export default PredictionEvaluation;