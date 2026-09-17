import React, { useState, useEffect, useRef } from 'react';
import { Card, Row, Col, Statistic, Tabs, Tag, Alert, Button, Spin } from 'antd';
import { 
  RocketOutlined, 
  CaretUpOutlined, 
  CaretDownOutlined, 
  ClockCircleOutlined,
  BarChartOutlined,
  MonitorOutlined,
  ReloadOutlined
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';



function LiveStats() {
  const [liveMatches, setLiveMatches] = useState([]);
  const [oddsHistory, setOddsHistory] = useState({});
  const [loading, setLoading] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const intervalRef = useRef(null);

  useEffect(() => {
    fetchLiveData();
    
    if (autoRefresh) {
      intervalRef.current = setInterval(fetchLiveData, 5000);
    }
    
    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
      }
    };
  }, [autoRefresh]);

  const fetchLiveData = () => {
    setLoading(true);
    
    setTimeout(() => {
      const mockMatches = [
        {
          id: 1,
          homeTeam: '曼城',
          awayTeam: '利物浦',
          homeScore: 2,
          awayScore: 1,
          minute: 67,
          status: 'live',
          odds: {
            home: 2.15,
            draw: 3.20,
            away: 3.40,
            over25: 1.75,
            under25: 2.10
          },
          oddsChange: {
            home: -0.05,
            draw: 0.10,
            away: 0.15,
            over25: -0.05,
            under25: 0.05
          },
          xg: { home: 1.8, away: 1.2 },
          possession: { home: 58, away: 42 },
          shots: { home: 12, away: 8 },
          shotsOnTarget: { home: 6, away: 3 }
        },
        {
          id: 2,
          homeTeam: '皇马',
          awayTeam: '巴萨',
          homeScore: 1,
          awayScore: 1,
          minute: 45,
          status: 'half-time',
          odds: {
            home: 2.00,
            draw: 3.10,
            away: 3.50,
            over25: 1.80,
            under25: 2.05
          },
          oddsChange: {
            home: 0,
            draw: -0.05,
            away: 0,
            over25: 0,
            under25: 0
          },
          xg: { home: 0.9, away: 1.1 },
          possession: { home: 48, away: 52 },
          shots: { home: 7, away: 9 },
          shotsOnTarget: { home: 3, away: 4 }
        },
        {
          id: 3,
          homeTeam: '尤文图斯',
          awayTeam: 'AC米兰',
          homeScore: 0,
          awayScore: 0,
          minute: 15,
          status: 'live',
          odds: {
            home: 2.25,
            draw: 3.25,
            away: 3.10,
            over25: 1.85,
            under25: 2.00
          },
          oddsChange: {
            home: 0.10,
            draw: 0,
            away: -0.10,
            over25: 0.05,
            under25: -0.05
          },
          xg: { home: 0.3, away: 0.2 },
          possession: { home: 62, away: 38 },
          shots: { home: 4, away: 2 },
          shotsOnTarget: { home: 1, away: 0 }
        },
        {
          id: 4,
          homeTeam: '拜仁',
          awayTeam: '多特',
          homeScore: 3,
          awayScore: 0,
          minute: 90,
          status: 'completed',
          odds: {
            home: 1.75,
            draw: 3.50,
            away: 4.50,
            over25: 1.65,
            under25: 2.25
          },
          oddsChange: {
            home: -0.30,
            draw: 0.50,
            away: 1.00,
            over25: -0.25,
            under25: 0.50
          },
          xg: { home: 2.5, away: 0.4 },
          possession: { home: 65, away: 35 },
          shots: { home: 18, away: 4 },
          shotsOnTarget: { home: 10, away: 1 }
        }
      ];
      
      setLiveMatches(mockMatches);
      
      mockMatches.forEach(match => {
        const key = `${match.homeTeam}-${match.awayTeam}`;
        setOddsHistory(prev => {
          const history = prev[key] || {
            home: [],
            draw: [],
            away: []
          };
          
          const maxLen = 20;
          return {
            ...prev,
            [key]: {
              home: [...history.home.slice(-maxLen), match.odds.home],
              draw: [...history.draw.slice(-maxLen), match.odds.draw],
              away: [...history.away.slice(-maxLen), match.odds.away]
            }
          };
        });
      });
      
      setLoading(false);
    }, 800);
  };

  const getStatusTag = (status) => {
    const statusMap = {
      'live': { color: 'red', text: '直播中' },
      'half-time': { color: 'orange', text: '半场' },
      'completed': { color: 'green', text: '已结束' }
    };
    const config = statusMap[status] || { color: 'default', text: status };
    return <Tag color={config.color}>{config.text}</Tag>;
  };

  const getOddsChangeIcon = (change) => {
    if (change > 0) return <CaretUpOutlined style={{ color: '#ef4444' }} />;
    if (change < 0) return <CaretDownOutlined style={{ color: '#10b981' }} />;
    return null;
  };

  const getMinuteDisplay = (minute, status) => {
    if (status === 'completed') return '90+';
    if (status === 'half-time') return '45';
    return `${minute}'`;
  };

  const createOddsChartOption = (match) => {
    const key = `${match.homeTeam}-${match.awayTeam}`;
    const history = oddsHistory[key];
    
    if (!history || history.home.length < 2) return null;
    
    const xData = history.home.map((_, i) => `T${i + 1}`);
    
    return {
      title: { 
        text: '赔率走势', 
        left: 'center',
        textStyle: { fontSize: 12, color: '#94a3b8' }
      },
      tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
      legend: { 
        data: ['主胜', '平局', '客胜'],
        bottom: 0,
        textStyle: { fontSize: 10, color: '#94a3b8' }
      },
      grid: {
        left: '3%',
        right: '4%',
        bottom: '15%',
        top: '15%',
        containLabel: true
      },
      xAxis: { 
        type: 'category', 
        data: xData,
        axisLabel: { fontSize: 8, color: '#94a3b8' },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
      },
      yAxis: { 
        type: 'value',
        axisLabel: { fontSize: 8, color: '#94a3b8' },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
      },
      series: [
        { name: '主胜', type: 'line', data: history.home, smooth: true, lineStyle: { width: 2 }, itemStyle: { color: '#3b82f6' } },
        { name: '平局', type: 'line', data: history.draw, smooth: true, lineStyle: { width: 2 }, itemStyle: { color: '#f59e0b' } },
        { name: '客胜', type: 'line', data: history.away, smooth: true, lineStyle: { width: 2 }, itemStyle: { color: '#ef4444' } }
      ]
    };
  };

  const createMatchStatsOption = (match) => {
    return {
      title: { 
        text: '实时统计', 
        left: 'center',
        textStyle: { fontSize: 12, color: '#94a3b8' }
      },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { color: '#f1f5f9' } },
      grid: {
        left: '3%',
        right: '4%',
        bottom: '3%',
        top: '15%',
        containLabel: true
      },
      xAxis: { 
        type: 'value',
        max: 100,
        axisLabel: { fontSize: 8, color: '#94a3b8' },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
      },
      yAxis: { 
        type: 'category', 
        data: ['控球率', '射门', '射正', 'xG'],
        axisLabel: { fontSize: 10, color: '#94a3b8' },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
      },
      series: [
        {
          name: match.homeTeam,
          type: 'bar',
          data: [
            match.possession.home,
            match.shots.home,
            match.shotsOnTarget.home,
            match.xg.home * 20
          ],
          itemStyle: { color: '#3b82f6' },
          barWidth: '30%'
        },
        {
          name: match.awayTeam,
          type: 'bar',
          data: [
            match.possession.away,
            match.shots.away,
            match.shotsOnTarget.away,
            match.xg.away * 20
          ],
          itemStyle: { color: '#ef4444' },
          barWidth: '30%'
        }
      ]
    };
  };

  const createLiveGaugeOption = (minute) => {
    const progress = Math.min(minute / 90, 1) * 100;
    return {
      series: [{
        type: 'gauge',
        startAngle: 200,
        endAngle: -20,
        min: 0,
        max: 90,
        splitNumber: 9,
        axisLine: {
          lineStyle: {
            width: 15,
            color: [
              [0.5, '#10b981'],
              [0.75, '#f59e0b'],
              [1, '#ef4444']
            ]
          }
        },
        pointer: { itemStyle: { color: '#fff' }, length: '60%', width: 4 },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        detail: {
          valueAnimation: true,
          formatter: `{value}'`,
          color: '#fff',
          fontSize: 24,
          fontWeight: 'bold',
          offsetCenter: [0, '50%']
        },
        data: [{ value: minute }],
        title: {
          offsetCenter: [0, '80%'],
          color: '#fff',
          fontSize: 10,
          formatter: '比赛时间'
        }
      }],
      backgroundColor: 'transparent'
    };
  };

  const liveSummaryOption = {
    tooltip: { trigger: 'item', textStyle: { color: '#f1f5f9' } },
    legend: { bottom: 0, textStyle: { fontSize: 10, color: '#94a3b8' } },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 8, borderColor: '#1e293b', borderWidth: 2 },
      label: { show: false },
      emphasis: {
        label: { show: true, fontSize: 12, fontWeight: 'bold', color: '#f1f5f9' }
      },
      labelLine: { show: false },
      data: [
        { value: liveMatches.filter(m => m.status === 'live').length, name: '直播中', itemStyle: { color: '#ef4444' } },
        { value: liveMatches.filter(m => m.status === 'half-time').length, name: '半场', itemStyle: { color: '#f59e0b' } },
        { value: liveMatches.filter(m => m.status === 'completed').length, name: '已结束', itemStyle: { color: '#10b981' } }
      ]
    }]
  };

  return (
    <div className="fade-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <h2 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
            <MonitorOutlined style={{ color: '#3b82f6' }} />
            <span style={{ background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>实时数据监控</span>
          </h2>
        </div>
        <div style={{ display: 'flex', gap: 12 }}>
          <Button 
            type={autoRefresh ? 'primary' : 'default'} 
            icon={<ReloadOutlined spin={autoRefresh} />}
            onClick={() => setAutoRefresh(!autoRefresh)}
          >
            {autoRefresh ? '自动刷新中' : '手动刷新'}
          </Button>
          <Button onClick={fetchLiveData} icon={<ReloadOutlined />}>
            刷新数据
          </Button>
        </div>
      </div>

      <Alert 
        type="info"
        message="数据每5秒自动更新，包含实时赔率、比赛统计和进度信息"
        showIcon
        style={{ marginBottom: 24 }}
      />

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={8}>
          <Card hoverable>
            <Statistic 
              title="直播比赛" 
              value={liveMatches.filter(m => m.status === 'live').length} 
              prefix={<RocketOutlined style={{ color: '#ef4444' }} />}
              valueStyle={{ color: '#ef4444', fontSize: 32 }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card hoverable>
            <Statistic 
              title="进行中" 
              value={liveMatches.filter(m => m.status !== 'completed').length} 
              prefix={<ClockCircleOutlined style={{ color: '#f59e0b' }} />}
              valueStyle={{ color: '#f59e0b', fontSize: 32 }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card hoverable>
            <Statistic 
              title="已结束" 
              value={liveMatches.filter(m => m.status === 'completed').length} 
              prefix={<BarChartOutlined style={{ color: '#10b981' }} />}
              valueStyle={{ color: '#10b981', fontSize: 32 }}
            />
          </Card>
        </Col>
      </Row>

      <Tabs defaultActiveKey="1" size="large" items={[
        { 
          key: '1', 
          label: '比赛列表', 
          children: (
            <Spin spinning={loading}>
              <Row gutter={16}>
                {liveMatches.map(match => (
                  <Col xs={24} sm={12} lg={6} key={match.id} style={{ marginBottom: 16 }}>
                    <Card 
                      hoverable
                      bordered={match.status === 'live'}
                      style={{ 
                        borderLeft: match.status === 'live' ? '4px solid #ef4444' : '4px solid rgba(255,255,255,0.1)',
                        transition: 'all 0.3s ease'
                      }}
                    >
                      <div style={{ textAlign: 'center', marginBottom: 16 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                          <span style={{ fontSize: 14, fontWeight: 'bold', color: '#f1f5f9' }}>{match.homeTeam}</span>
                          {getStatusTag(match.status)}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
                          <span style={{ fontSize: 28, fontWeight: 'bold', color: '#f1f5f9' }}>{match.homeScore}</span>
                          <span style={{ fontSize: 18, color: '#64748b' }}>-</span>
                          <span style={{ fontSize: 28, fontWeight: 'bold', color: '#f1f5f9' }}>{match.awayScore}</span>
                        </div>
                        <div style={{ fontSize: 14, fontWeight: 'bold', color: '#f1f5f9', marginTop: 8 }}>{match.awayTeam}</div>
                        <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>{getMinuteDisplay(match.minute, match.status)}</div>
                      </div>
                      <div style={{ marginBottom: 12 }}>
                        <div style={{ fontSize: 12, color: '#64748b', marginBottom: 8 }}>实时赔率</div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontWeight: 'bold', color: '#f1f5f9' }}>{match.odds.home.toFixed(2)}</div>
                            <div style={{ fontSize: 10, color: '#64748b' }}>主胜</div>
                            {getOddsChangeIcon(match.oddsChange.home)}
                          </div>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontWeight: 'bold', color: '#f1f5f9' }}>{match.odds.draw.toFixed(2)}</div>
                            <div style={{ fontSize: 10, color: '#64748b' }}>平局</div>
                            {getOddsChangeIcon(match.oddsChange.draw)}
                          </div>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontWeight: 'bold', color: '#f1f5f9' }}>{match.odds.away.toFixed(2)}</div>
                            <div style={{ fontSize: 10, color: '#64748b' }}>客胜</div>
                            {getOddsChangeIcon(match.oddsChange.away)}
                          </div>
                        </div>
                      </div>
                      <div>
                        <div style={{ fontSize: 12, color: '#64748b', marginBottom: 8 }}>总进球</div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontWeight: 'bold', color: match.oddsChange.over25 < 0 ? '#10b981' : '#f1f5f9' }}>
                              {match.odds.over25.toFixed(2)}
                            </div>
                            <div style={{ fontSize: 10, color: '#64748b' }}>大球</div>
                          </div>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontWeight: 'bold', color: match.oddsChange.under25 < 0 ? '#10b981' : '#f1f5f9' }}>
                              {match.odds.under25.toFixed(2)}
                            </div>
                            <div style={{ fontSize: 10, color: '#64748b' }}>小球</div>
                          </div>
                        </div>
                      </div>
                    </Card>
                  </Col>
                ))}
              </Row>
            </Spin>
          )
        },
        { 
          key: '2', 
          label: '实时统计', 
          children: (
            <Spin spinning={loading}>
              <Row gutter={16}>
                {liveMatches.filter(m => m.status !== 'completed').map(match => (
                  <Col xs={24} md={12} key={match.id} style={{ marginBottom: 16 }}>
                    <Card title={<span style={{ color: '#f1f5f9' }}>{match.homeTeam} vs {match.awayTeam}</span>}>
                      <Row gutter={16}>
                        <Col xs={6}>
                          <div style={{ 
                            display: 'flex', 
                            alignItems: 'center', 
                            justifyContent: 'center',
                            background: 'linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)',
                            borderRadius: 8,
                            padding: 16,
                            color: '#fff'
                          }}>
                            <ReactECharts 
                              option={createLiveGaugeOption(match.minute)} 
                              style={{ height: 120, width: '100%' }}
                              opts={{ renderer: 'svg' }}
                            />
                          </div>
                        </Col>
                        <Col xs={18}>
                          <ReactECharts 
                            option={createMatchStatsOption(match)} 
                            style={{ height: 180, width: '100%' }}
                            opts={{ renderer: 'svg' }}
                          />
                        </Col>
                      </Row>
                    </Card>
                  </Col>
                ))}
              </Row>
            </Spin>
          )
        },
        { 
          key: '3', 
          label: '赔率走势', 
          children: (
            <Spin spinning={loading}>
              <Row gutter={16}>
                {liveMatches.map(match => (
                  <Col xs={24} md={12} key={match.id} style={{ marginBottom: 16 }}>
                    <Card title={<span style={{ color: '#f1f5f9' }}>{match.homeTeam} vs {match.awayTeam}</span>}>
                      <ReactECharts 
                        option={createOddsChartOption(match)} 
                        style={{ height: 200, width: '100%' }}
                        opts={{ renderer: 'svg' }}
                      />
                    </Card>
                  </Col>
                ))}
              </Row>
            </Spin>
          )
        },
        { 
          key: '4', 
          label: '数据概览', 
          children: (
            <Row gutter={16}>
              <Col xs={24} sm={12}>
                <Card title="比赛状态分布">
                  <ReactECharts 
                    option={liveSummaryOption} 
                    style={{ height: 250, width: '100%' }}
                    opts={{ renderer: 'svg' }}
                  />
                </Card>
              </Col>
              <Col xs={24} sm={12}>
                <Card title="比赛统计汇总">
                  <div style={{ padding: '0 16px' }}>
                    {liveMatches.filter(m => m.status !== 'completed').map((match, index) => (
                      <div key={match.id} style={{ 
                        display: 'flex', 
                        justifyContent: 'space-between', 
                        padding: '8px 0',
                        borderBottom: index < liveMatches.filter(m => m.status !== 'completed').length - 1 ? '1px solid rgba(255,255,255,0.05)' : 'none'
                      }}>
                        <span style={{ color: '#f1f5f9' }}>{match.homeTeam} {match.homeScore}-{match.awayScore} {match.awayTeam}</span>
                        <span style={{ color: '#64748b' }}>
                          xG: {match.xg.home.toFixed(1)}-{match.xg.away.toFixed(1)}
                        </span>
                      </div>
                    ))}
                  </div>
                </Card>
              </Col>
            </Row>
          )
        }
      ]} />
    </div>
  );
}

export default LiveStats;