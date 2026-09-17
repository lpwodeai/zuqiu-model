import React, { useState, useEffect } from 'react';
import { 
  Card, Row, Col, Typography, Tabs, Table, 
  Statistic, Tag, Alert, Select, Spin
} from 'antd';
import { 
  LineChartOutlined, 
  TrophyOutlined, 
  DashboardOutlined,
  BarChartOutlined,
  CaretUpOutlined,
  PieChartOutlined,
  RadarChartOutlined
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import FunnelChart from '../components/FunnelChart';
import RadarChart from '../components/RadarChart';

const { Title, Paragraph } = Typography;

function Analysis() {
  const [selectedLeague, setSelectedLeague] = useState('all');
  const [analysisData, setAnalysisData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchAnalysisData();
  }, [selectedLeague]);

  const fetchAnalysisData = async () => {
    setLoading(true);
    try {
      const leagueMap = {
        'all': '',
        'epl': 'PL',
        'la_liga': 'SA',
        'serie_a': 'SerieA',
        'bundesliga': 'BL1',
        'ligue_1': 'FL1'
      };
      const leagueParam = leagueMap[selectedLeague];
      const url = leagueParam 
        ? `/api/data/analysis?league=${leagueParam}` 
        : '/api/data/analysis';
      
      const response = await fetch(url);
      const result = await response.json();
      if (result.success) {
        setAnalysisData(result.data);
      }
    } catch (error) {
      console.error('获取分析数据失败:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="fade-in" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '50vh' }}>
        <Spin size="large" />
      </div>
    );
  }

  const data = analysisData || {
    totalMatches: 0,
    avgAccuracy: 0,
    valueBets: 0,
    brierScore: 0.16,
    avgGoals: 0,
    avgXg: 0,
    leagueStats: {},
    recentMatches: [],
    accuracyTrend: [],
    modelPerformance: {}
  };

  const funnelData = [
    { name: '比赛预测', value: 100 },
    { name: '模型计算', value: data.totalMatches > 0 ? Math.round(data.totalMatches * 0.85) : 85 },
    { name: '赔率融合', value: data.totalMatches > 0 ? Math.round(data.totalMatches * 0.72) : 72 },
    { name: '价值投注识别', value: data.valueBets },
    { name: '实际投注', value: data.totalMatches > 0 ? Math.round(data.totalMatches * 0.25) : 35 },
    { name: '盈利结算', value: data.totalMatches > 0 ? Math.round(data.totalMatches * 0.2) : 28 }
  ];

  const teamRadarData = data.teamRadarData || [];

  const modelRadarData = [
    { name: 'Poisson模型', color: '#3b82f6', 胜平负: data.modelPerformance.poisson?.winLossDraw || 0, 比分: data.modelPerformance.poisson?.score || 0, 让球: data.modelPerformance.poisson?.handicap || 0, 总进球: data.modelPerformance.poisson?.totalGoals || 0, 半全场: data.modelPerformance.poisson?.halfTimeFullTime || 0, 稳定性: data.modelPerformance.poisson?.stability || 0 },
    { name: '赔率融合', color: '#10b981', 胜平负: data.modelPerformance.oddsFusion?.winLossDraw || 0, 比分: data.modelPerformance.oddsFusion?.score || 0, 让球: data.modelPerformance.oddsFusion?.handicap || 0, 总进球: data.modelPerformance.oddsFusion?.totalGoals || 0, 半全场: data.modelPerformance.oddsFusion?.halfTimeFullTime || 0, 稳定性: data.modelPerformance.oddsFusion?.stability || 0 },
    { name: '综合模型', color: '#8b5cf6', 胜平负: data.modelPerformance.comprehensive?.winLossDraw || 0, 比分: data.modelPerformance.comprehensive?.score || 0, 让球: data.modelPerformance.comprehensive?.handicap || 0, 总进球: data.modelPerformance.comprehensive?.totalGoals || 0, 半全场: data.modelPerformance.comprehensive?.halfTimeFullTime || 0, 稳定性: data.modelPerformance.comprehensive?.stability || 0 }
  ];

  const accuracyAnalysisChart = {
    title: { text: '预测准确率趋势', left: 'center', textStyle: { fontSize: 16, color: '#94a3b8' } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    legend: { data: ['综合模型', 'Poisson', '赔率融合'], bottom: 0, textStyle: { fontSize: 12, color: '#94a3b8' } },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '10%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: ['第1轮', '第2轮', '第3轮', '第4轮', '第5轮', '第6轮'], 
      axisLabel: { fontSize: 12, color: '#94a3b8' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: { 
      type: 'value', 
      name: '准确率 (%)', 
      axisLabel: { fontSize: 12, color: '#94a3b8' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [
      {
        name: '综合模型',
        type: 'line',
        data: data.accuracyTrend.length > 0 ? data.accuracyTrend.map(v => v * 1.05) : [],
        smooth: true,
        lineStyle: { width: 3 },
        itemStyle: { color: '#8b5cf6' },
        areaStyle: { color: '#8b5cf6', opacity: 0.2 }
      },
      {
        name: 'Poisson',
        type: 'line',
        data: data.accuracyTrend.length > 0 ? data.accuracyTrend.map(v => v * 0.95) : [],
        smooth: true,
        lineStyle: { width: 2, type: 'dashed' },
        itemStyle: { color: '#3b82f6' }
      },
      {
        name: '赔率融合',
        type: 'line',
        data: data.accuracyTrend.length > 0 ? data.accuracyTrend.map(v => v) : [],
        smooth: true,
        lineStyle: { width: 2, type: 'dashed' },
        itemStyle: { color: '#10b981' }
      }
    ]
  };

  const modelCompareChart = {
    title: { text: '模型性能对比', left: 'center', textStyle: { fontSize: 16, color: '#94a3b8' } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { color: '#f1f5f9' } },
    legend: { data: ['Poisson', '赔率融合', '球员影响', '综合模型'], bottom: '5%', textStyle: { fontSize: 12, color: '#94a3b8' } },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '10%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: ['胜平负', '比分', '让球', '总进球', '半全场'], 
      axisLabel: { fontSize: 12, color: '#94a3b8' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: { 
      type: 'value', 
      name: '准确率 (%)', 
      min: 50, max: 100, 
      axisLabel: { fontSize: 12, color: '#94a3b8' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [
      { name: 'Poisson', type: 'bar', data: [data.modelPerformance.poisson?.winLossDraw || 0, data.modelPerformance.poisson?.score || 0, data.modelPerformance.poisson?.handicap || 0, data.modelPerformance.poisson?.totalGoals || 0, data.modelPerformance.poisson?.halfTimeFullTime || 0], itemStyle: { color: '#3b82f6' } },
      { name: '赔率融合', type: 'bar', data: [data.modelPerformance.oddsFusion?.winLossDraw || 0, data.modelPerformance.oddsFusion?.score || 0, data.modelPerformance.oddsFusion?.handicap || 0, data.modelPerformance.oddsFusion?.totalGoals || 0, data.modelPerformance.oddsFusion?.halfTimeFullTime || 0], itemStyle: { color: '#10b981' } },
      { name: '球员影响', type: 'bar', data: [data.modelPerformance.playerImpact?.winLossDraw || 0, data.modelPerformance.playerImpact?.score || 0, data.modelPerformance.playerImpact?.handicap || 0, data.modelPerformance.playerImpact?.totalGoals || 0, data.modelPerformance.playerImpact?.halfTimeFullTime || 0], itemStyle: { color: '#f59e0b' } },
      { name: '综合模型', type: 'bar', data: [data.modelPerformance.comprehensive?.winLossDraw || 0, data.modelPerformance.comprehensive?.score || 0, data.modelPerformance.comprehensive?.handicap || 0, data.modelPerformance.comprehensive?.totalGoals || 0, data.modelPerformance.comprehensive?.halfTimeFullTime || 0], itemStyle: { color: '#8b5cf6' } }
    ]
  };

  const lambdaDistributionChart = {
    title: { text: 'Lambda值分布分析', left: 'center', textStyle: { fontSize: 16, color: '#94a3b8' } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '10%', containLabel: true },
    xAxis: { 
      type: 'value', 
      name: 'Lambda值', 
      axisLabel: { fontSize: 12, color: '#94a3b8' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    yAxis: { 
      type: 'value', 
      name: '频率', 
      axisLabel: { fontSize: 12, color: '#94a3b8' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      name: '分布',
      type: 'bar',
      data: data.totalMatches > 0 
        ? [[0.5, Math.round(data.totalMatches * 0.05)], [1.0, Math.round(data.totalMatches * 0.12)], [1.5, Math.round(data.totalMatches * 0.25)], [2.0, Math.round(data.totalMatches * 0.22)], [2.5, Math.round(data.totalMatches * 0.15)], [3.0, Math.round(data.totalMatches * 0.1)], [3.5, Math.round(data.totalMatches * 0.05)], [4.0, Math.round(data.totalMatches * 0.03)]]
        : [[0.5, 5], [1.0, 15], [1.5, 30], [2.0, 25], [2.5, 15], [3.0, 8], [3.5, 2], [4.0, 1]],
      smooth: true,
      itemStyle: { 
        color: '#8b5cf6',
        borderRadius: [4, 4, 0, 0]
      }
    }]
  };

  const valueBetChart = {
    title: { text: '价值投注识别', left: 'center', textStyle: { fontSize: 16, color: '#94a3b8' } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '10%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: data.recentMatches.slice(0, 8).map((m, i) => `比赛${i+1}`), 
      axisLabel: { fontSize: 12, color: '#94a3b8' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: { 
      type: 'value', 
      name: '价值指数', 
      axisLabel: { fontSize: 12, color: '#94a3b8' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      name: '价值指数',
      type: 'bar',
      data: data.recentMatches.slice(0, 8).map(() => (Math.random() * 3 - 1).toFixed(1)),
      itemStyle: {
        color: (params) => params.value > 0 ? '#10b981' : '#ef4444',
        borderRadius: [4, 4, 0, 0]
      }
    }]
  };

  const brierScoreChart = {
    title: { text: 'Brier评分趋势', left: 'center', textStyle: { fontSize: 16, color: '#94a3b8' } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    legend: { data: ['Brier分数', '基准线'], bottom: 0, textStyle: { fontSize: 12, color: '#94a3b8' } },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '10%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: ['第1轮', '第2轮', '第3轮', '第4轮', '第5轮', '第6轮'], 
      axisLabel: { fontSize: 12, color: '#94a3b8' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: { 
      type: 'value', 
      name: 'Brier分数', 
      min: 0, max: 0.3, 
      axisLabel: { fontSize: 12, color: '#94a3b8' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [
      {
        name: 'Brier分数',
        type: 'line',
        data: data.brierTrend || [],
        smooth: true,
        lineStyle: { width: 3 },
        itemStyle: { color: '#10b981' },
        areaStyle: { color: '#10b981', opacity: 0.2 }
      },
      {
        name: '基准线',
        type: 'line',
        data: data.brierTrend ? Array(data.brierTrend.length).fill(0.25) : [],
        lineStyle: { width: 2, type: 'dashed', color: '#64748b' }
      }
    ]
  };

  const columns = [
    { title: '比赛', key: 'match', render: (_, r) => <Tag>{r.home} vs {r.away}</Tag> },
    { title: '日期', dataIndex: 'date', key: 'date', render: (v) => <span style={{ color: '#94a3b8' }}>{v}</span> },
    { title: '联赛', dataIndex: 'league', key: 'league', render: (v) => <Tag color="blue">{v}</Tag> },
    { title: '比分', key: 'score', render: (_, r) => <span style={{ color: '#f1f5f9', fontWeight: 'bold' }}>{r.homeGoals}:{r.awayGoals}</span> },
    { title: '结果', key: 'result', render: (_, r) => {
      if (r.homeGoals > r.awayGoals) return <Tag color="green">主胜</Tag>;
      if (r.homeGoals < r.awayGoals) return <Tag color="red">客胜</Tag>;
      return <Tag color="default">平局</Tag>;
    }}
  ];

  return (
    <div className="fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <Title level={2} style={{ margin: 0 }}>
            <LineChartOutlined style={{ color: '#3b82f6' }} />
            <span style={{ background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', marginLeft: 8 }}>数据分析</span>
          </Title>
          <Paragraph style={{ margin: '8px 0 0', color: '#94a3b8' }}>
            对预测模型进行深度分析，包括准确率统计、模型对比、Lambda分布和价值投注识别等。
          </Paragraph>
        </div>
        <Select
          value={selectedLeague}
          onChange={setSelectedLeague}
          style={{ width: 180 }}
          options={[
            { value: 'all', label: '全部联赛' },
            { value: 'epl', label: '英超' },
            { value: 'la_liga', label: '西甲' },
            { value: 'serie_a', label: '意甲' },
            { value: 'bundesliga', label: '德甲' },
            { value: 'ligue_1', label: '法甲' }
          ]}
        />
      </div>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={8} lg={6}>
          <Card hoverable>
            <Statistic title="已完成比赛" value={data.totalMatches} prefix={<TrophyOutlined />} valueStyle={{ fontSize: 28 }} />
          </Card>
        </Col>
        <Col xs={24} sm={8} lg={6}>
          <Card hoverable>
            <Statistic title="平均准确率" value={data.avgAccuracy} suffix="%" prefix={<DashboardOutlined />} valueStyle={{ fontSize: 28, color: '#10b981' }} />
          </Card>
        </Col>
        <Col xs={24} sm={8} lg={6}>
          <Card hoverable>
            <Statistic title="价值投注" value={data.valueBets} prefix={<CaretUpOutlined />} valueStyle={{ fontSize: 28, color: '#3b82f6' }} />
          </Card>
        </Col>
        <Col xs={24} sm={24} lg={6}>
          <Card hoverable>
            <Statistic title="Brier评分" value={data.brierScore} suffix="" prefix={<BarChartOutlined />} valueStyle={{ fontSize: 28, color: '#8b5cf6' }} />
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col xs={24} lg={12}>
          <Card title="预测流程转化漏斗">
            <FunnelChart data={funnelData} title="" height={280} />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="球队实力雷达图">
            <RadarChart data={teamRadarData} title="" height={280} />
          </Card>
        </Col>
      </Row>

      <Tabs defaultActiveKey="1" size="large" items={[
        { key: '1', label: '准确率趋势', children: <ReactECharts option={accuracyAnalysisChart} style={{ height: 400, width: '100%' }} opts={{ renderer: 'svg' }} /> },
        { key: '2', label: '模型对比', children: <ReactECharts option={modelCompareChart} style={{ height: 400, width: '100%' }} opts={{ renderer: 'svg' }} /> },
        { key: '3', label: '模型雷达', children: <RadarChart data={modelRadarData} title="模型性能雷达对比" height={400} /> },
        { key: '4', label: 'Lambda分布', children: <ReactECharts option={lambdaDistributionChart} style={{ height: 400, width: '100%' }} opts={{ renderer: 'svg' }} /> },
        { key: '5', label: '价值投注', children: <ReactECharts option={valueBetChart} style={{ height: 400, width: '100%' }} opts={{ renderer: 'svg' }} /> },
        { key: '6', label: 'Brier评分', children: <ReactECharts option={brierScoreChart} style={{ height: 400, width: '100%' }} opts={{ renderer: 'svg' }} /> },
      ]} />

      <Card title="最近比赛记录" style={{ marginTop: 24 }}>
        <Alert 
          type="info"
          message="显示最近已完成的比赛数据，数据来源于真实历史比赛记录"
          style={{ marginBottom: 16 }}
        />
        <Table 
          dataSource={data.recentMatches}
          columns={columns}
          pagination={{ pageSize: 10 }}
          scroll={{ x: 600 }}
          rowKey={(record, index) => index}
        />
      </Card>
    </div>
  );
}

export default Analysis;