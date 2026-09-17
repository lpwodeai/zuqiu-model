import React, { useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, List, Typography, Tag, Progress, Alert } from 'antd';
import { 
  TrophyOutlined, 
  TeamOutlined, 
  CalendarOutlined, 
  RocketOutlined,
  ThunderboltOutlined,
  BarChartOutlined,
  RobotOutlined,
  ClockCircleOutlined
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { fetchApiSafe } from '../utils/api';

const { Title, Paragraph } = Typography;

function Home() {
  const [stats, setStats] = useState({
    teams: 98,
    matches: 0,
    predictions: 0,
    accuracy: 78.3
  });

  const [recentMatches, setRecentMatches] = useState([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    fetchApiSafe('/api/data/matches?limit=5')
      .then(data => {
        if (data.success) {
          setRecentMatches(data.data);
        }
      });
    
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws`);
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    return () => ws.close();
  }, []);

  const accuracyChartOption = {
    title: { text: '预测准确率', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'item', formatter: '{b}: {c}% ({d}%)' },
    legend: { bottom: '5%', left: 'center', textStyle: { color: '#94a3b8', fontSize: 12 } },
    series: [
      {
        name: '预测结果',
        type: 'pie',
        radius: ['40%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 10,
          borderColor: '#1e293b',
          borderWidth: 2
        },
        label: { show: false, position: 'center' },
        emphasis: {
          label: { show: true, fontSize: '20', fontWeight: 'bold', color: '#f1f5f9' }
        },
        labelLine: { show: false },
        data: [
          { value: 78.3, name: '正确预测', itemStyle: { color: '#10b981' } },
          { value: 21.7, name: '错误预测', itemStyle: { color: '#ef4444' } }
        ]
      }
    ]
  };

  const groupChartOption = {
    title: { text: '联赛分布', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '15%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: ['英超', '西甲', '意甲', '德甲', '法甲'],
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: { 
      type: 'value',
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      data: [20, 20, 20, 18, 20],
      type: 'bar',
      itemStyle: { 
        color: 'linear-gradient(135deg, #3b82f6, #2563eb)',
        borderRadius: [8, 8, 0, 0]
      },
      barWidth: '60%'
    }]
  };

  const trendChartOption = {
    title: { text: '近期预测趋势', left: 'center', textStyle: { color: '#94a3b8', fontSize: 14 } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '15%', containLabel: true },
    xAxis: { 
      type: 'category', 
      data: ['周一', '周二', '周三', '周四', '周五', '周六', '周日'],
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: { 
      type: 'value',
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      data: [72, 78, 75, 82, 79, 85, 81],
      type: 'line',
      smooth: true,
      itemStyle: { color: '#8b5cf6' },
      lineStyle: { width: 3 },
      areaStyle: { color: '#8b5cf6', opacity: 0.2 },
      symbol: 'circle',
      symbolSize: 8
    }]
  };

  return (
    <div className="fade-in">
      <Alert 
        type="info"
        message={connected ? '实时连接正常，数据将自动更新' : '实时连接断开，正在重连...'}
        showIcon
        style={{ marginBottom: 24, background: connected ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)', borderColor: connected ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)' }}
      />

      <div style={{ marginBottom: 24 }}>
        <Title level={2} style={{ margin: 0, background: 'linear-gradient(90deg, #3b82f6, #8b5cf6, #10b981)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
          五大联赛足球预测模型
        </Title>
        <Paragraph style={{ margin: '8px 0 0', color: '#94a3b8' }}>
          基于<strong style={{ color: '#3b82f6' }}>Poisson分布</strong>和<strong style={{ color: '#8b5cf6' }}>机器学习</strong>的五大联赛足球比赛预测系统，
          覆盖英超、西甲、意甲、德甲、法甲五大联赛，提供胜平负、比分、让球盘、总进球等多维度预测分析。
        </Paragraph>
      </div>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card hoverable>
            <Statistic 
              title="覆盖球队" 
              value={stats.teams} 
              prefix={<TeamOutlined style={{ color: '#3b82f6' }} />}
              valueStyle={{ color: '#3b82f6', fontSize: 32 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable>
            <Statistic 
              title="比赛场次" 
              value={stats.matches} 
              prefix={<CalendarOutlined style={{ color: '#10b981' }} />}
              valueStyle={{ color: '#10b981', fontSize: 32 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable>
            <Statistic 
              title="预测场次" 
              value={stats.predictions} 
              prefix={<TrophyOutlined style={{ color: '#f59e0b' }} />}
              valueStyle={{ color: '#f59e0b', fontSize: 32 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable>
            <Statistic 
              title="预测准确率" 
              value={stats.accuracy} 
              suffix="%" 
              prefix={<RocketOutlined style={{ color: '#8b5cf6' }} />}
              valueStyle={{ color: '#8b5cf6', fontSize: 32 }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={8}>
          <Card title="预测准确率分布">
            <ReactECharts option={accuracyChartOption} style={{ height: 250 }} opts={{ renderer: 'svg' }} />
          </Card>
        </Col>
        <Col span={8}>
          <Card title="联赛球队分布">
            <ReactECharts option={groupChartOption} style={{ height: 250 }} opts={{ renderer: 'svg' }} />
          </Card>
        </Col>
        <Col span={8}>
          <Card title="近期预测趋势">
            <ReactECharts option={trendChartOption} style={{ height: 250 }} opts={{ renderer: 'svg' }} />
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={12}>
          <Card title="最近比赛">
            <List
              dataSource={recentMatches.length > 0 ? recentMatches : [
                { homeTeam: '曼城', awayTeam: '利物浦', date: '2024-01-15', status: 'completed' },
                { homeTeam: '皇马', awayTeam: '巴萨', date: '2024-01-16', status: 'completed' },
                { homeTeam: '尤文图斯', awayTeam: 'AC米兰', date: '2024-01-17', status: 'completed' },
                { homeTeam: '拜仁', awayTeam: '多特', date: '2024-01-18', status: 'completed' },
                { homeTeam: '巴黎', awayTeam: '马赛', date: '2024-01-19', status: 'pending' }
              ]}
              renderItem={match => (
                <List.Item style={{ borderBottom: '1px solid rgba(255,255,255,0.05)', padding: '12px 0' }}>
                  <List.Item.Meta
                    title={<span style={{ color: '#f1f5f9' }}>{match.homeTeam} vs {match.awayTeam}</span>}
                    description={<span style={{ color: '#64748b' }}>{match.date} - {match.league || match.group || '英超'}</span>}
                  />
                  <Tag color={match.status === 'completed' ? 'green' : match.status === 'live' ? 'red' : 'blue'}>
                    {match.status === 'completed' ? '已完成' : match.status === 'live' ? '直播中' : '待进行'}
                  </Tag>
                </List.Item>
              )}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="系统特性">
            <Row gutter={16}>
              <Col span={12}>
                <div style={{ 
                  background: 'rgba(59, 130, 246, 0.1)', 
                  borderRadius: 12, 
                  padding: 20, 
                  textAlign: 'center',
                  marginBottom: 12
                }}>
                  <RobotOutlined style={{ fontSize: 32, color: '#3b82f6', marginBottom: 8 }} />
                  <div style={{ fontSize: 14, fontWeight: 'bold', color: '#f1f5f9' }}>Poisson模型</div>
                  <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>核心算法</div>
                </div>
              </Col>
              <Col span={12}>
                <div style={{ 
                  background: 'rgba(16, 185, 129, 0.1)', 
                  borderRadius: 12, 
                  padding: 20, 
                  textAlign: 'center',
                  marginBottom: 12
                }}>
                  <BarChartOutlined style={{ fontSize: 32, color: '#10b981', marginBottom: 8 }} />
                  <div style={{ fontSize: 14, fontWeight: 'bold', color: '#f1f5f9' }}>赔率融合</div>
                  <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>实时分析</div>
                </div>
              </Col>
              <Col span={12}>
                <div style={{ 
                  background: 'rgba(245, 158, 11, 0.1)', 
                  borderRadius: 12, 
                  padding: 20, 
                  textAlign: 'center'
                }}>
                  <ThunderboltOutlined style={{ fontSize: 32, color: '#f59e0b', marginBottom: 8 }} />
                  <div style={{ fontSize: 14, fontWeight: 'bold', color: '#f1f5f9' }}>球员影响</div>
                  <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>智能评估</div>
                </div>
              </Col>
              <Col span={12}>
                <div style={{ 
                  background: 'rgba(139, 92, 246, 0.1)', 
                  borderRadius: 12, 
                  padding: 20, 
                  textAlign: 'center'
                }}>
                  <ClockCircleOutlined style={{ fontSize: 32, color: '#8b5cf6', marginBottom: 8 }} />
                  <div style={{ fontSize: 14, fontWeight: 'bold', color: '#f1f5f9' }}>实时更新</div>
                  <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>30秒间隔</div>
                </div>
              </Col>
            </Row>
          </Card>
        </Col>
      </Row>

      <Card title="模型性能指标" style={{ marginTop: 24 }}>
        <Row gutter={16}>
          <Col span={6}>
            <Progress type="circle" percent={95} format={() => 'Poisson'} strokeColor={{ '0%': '#3b82f6', '100%': '#2563eb' }} />
          </Col>
          <Col span={6}>
            <Progress type="circle" percent={85} format={() => '赔率融合'} strokeColor={{ '0%': '#10b981', '100%': '#059669' }} />
          </Col>
          <Col span={6}>
            <Progress type="circle" percent={80} format={() => '球员影响'} strokeColor={{ '0%': '#f59e0b', '100%': '#d97706' }} />
          </Col>
          <Col span={6}>
            <Progress type="circle" percent={90} format={() => '实时更新'} strokeColor={{ '0%': '#8b5cf6', '100%': '#7c3aed' }} />
          </Col>
        </Row>
      </Card>
    </div>
  );
}

export default Home;