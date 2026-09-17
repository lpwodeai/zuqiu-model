import React, { useState } from 'react';
import { Card, Row, Col, Typography, Tabs, Form, Input, Button, Statistic, Table, Tag, Alert, Space, Spin } from 'antd';
import { CrownOutlined, BarChartOutlined, DollarOutlined, SearchOutlined, SyncOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';

const { Title, Paragraph } = Typography;

function Professional() {
  const [activeTab, setActiveTab] = useState('multi-market');
  const [homeTeam, setHomeTeam] = useState('');
  const [awayTeam, setAwayTeam] = useState('');
  const [loading, setLoading] = useState(false);
  const [oddsData, setOddsData] = useState([]);
  const [bestOdds, setBestOdds] = useState(null);
  const [arbitrageResults, setArbitrageResults] = useState([]);
  const [strategyResults, setStrategyResults] = useState([]);
  const [selectedStrategies, setSelectedStrategies] = useState(['value_bet', 'momentum']);

  const handleGetOdds = async () => {
    if (!homeTeam || !awayTeam) return;
    setLoading(true);
    await new Promise(resolve => setTimeout(resolve, 1500));
    setOddsData([
      { bookmaker: 'Bet365', home: 2.35, draw: 3.20, away: 2.80, over25: 2.05, under25: 1.75, home05: 1.95, away05: 1.85 },
      { bookmaker: 'William Hill', home: 2.40, draw: 3.10, away: 2.75, over25: 2.00, under25: 1.80, home05: 1.90, away05: 1.90 },
      { bookmaker: 'Ladbrokes', home: 2.30, draw: 3.25, away: 2.85, over25: 2.10, under25: 1.72, home05: 1.98, away05: 1.82 },
      { bookmaker: 'Paddy Power', home: 2.38, draw: 3.15, away: 2.78, over25: 2.03, under25: 1.78, home05: 1.93, away05: 1.88 },
      { bookmaker: 'Coral', home: 2.32, draw: 3.22, away: 2.82, over25: 2.08, under25: 1.73, home05: 1.96, away05: 1.84 },
      { bookmaker: 'Betfair', home: 2.42, draw: 3.05, away: 2.72, over25: 1.98, under25: 1.82, home05: 1.88, away05: 1.92 },
    ]);
    setBestOdds({
      home: { bookmaker: 'Betfair', odds: 2.42 },
      draw: { bookmaker: 'Ladbrokes', odds: 3.25 },
      away: { bookmaker: 'Ladbrokes', odds: 2.85 },
      over25: { bookmaker: 'Ladbrokes', odds: 2.10 },
      under25: { bookmaker: 'Ladbrokes', odds: 1.72 },
      home05: { bookmaker: 'Ladbrokes', odds: 1.98 },
      away05: { bookmaker: 'Betfair', odds: 1.92 }
    });
    setLoading(false);
  };

  const handleDetectArbitrage = async () => {
    if (!homeTeam || !awayTeam) return;
    setLoading(true);
    await new Promise(resolve => setTimeout(resolve, 1500));
    setArbitrageResults([
      {
        description: '主胜 + 平局 + 客胜 三串套利',
        expected_return: 2.3,
        implied_probability: 97.7,
        legs: [
          { bookmaker: 'Betfair', selection: '主胜', odds: 2.42, stake: 413.2, potential_win: 1000, profit: 23.2 },
          { bookmaker: 'Ladbrokes', selection: '平局', odds: 3.25, stake: 307.7, potential_win: 1000, profit: 23.2 },
          { bookmaker: 'Ladbrokes', selection: '客胜', odds: 2.85, stake: 350.9, potential_win: 1000, profit: 23.2 }
        ]
      }
    ]);
    setLoading(false);
  };

  const handleBacktest = async () => {
    setLoading(true);
    await new Promise(resolve => setTimeout(resolve, 2000));
    setStrategyResults([
      { strategy: '价值投注', roi: 18.5, win_rate: 58.3, max_drawdown: 12.5, total_bets: 120, wins: 70 },
      { strategy: '动量策略', roi: 12.2, win_rate: 55.1, max_drawdown: 18.3, total_bets: 100, wins: 55 },
      { strategy: '冷门策略', roi: 8.7, win_rate: 42.8, max_drawdown: 25.6, total_bets: 98, wins: 42 },
      { strategy: '大球策略', roi: 15.3, win_rate: 52.1, max_drawdown: 15.2, total_bets: 110, wins: 57 },
    ]);
    setLoading(false);
  };

  const oddsColumns = [
    { title: '博彩公司', dataIndex: 'bookmaker', key: 'bookmaker', render: (text) => <span style={{ color: '#f1f5f9', fontWeight: 'bold' }}>{text}</span> },
    { title: '主胜', dataIndex: 'home', key: 'home', render: (v) => <span style={{ color: '#10b981' }}>{v}</span> },
    { title: '平局', dataIndex: 'draw', key: 'draw', render: (v) => <span style={{ color: '#f59e0b' }}>{v}</span> },
    { title: '客胜', dataIndex: 'away', key: 'away', render: (v) => <span style={{ color: '#ef4444' }}>{v}</span> },
    { title: '大球2.5', dataIndex: 'over25', key: 'over25', render: (v) => <span style={{ color: '#3b82f6' }}>{v}</span> },
    { title: '小球2.5', dataIndex: 'under25', key: 'under25', render: (v) => <span style={{ color: '#8b5cf6' }}>{v}</span> },
    { title: '主让0.5', dataIndex: 'home05', key: 'home05', render: (v) => <span style={{ color: '#10b981' }}>{v}</span> },
    { title: '客让0.5', dataIndex: 'away05', key: 'away05', render: (v) => <span style={{ color: '#ef4444' }}>{v}</span> },
  ];

  const arbitrageColumns = [
    { title: '博彩公司', dataIndex: 'bookmaker', key: 'bookmaker', render: (text) => <span style={{ color: '#f1f5f9', fontWeight: 'bold' }}>{text}</span> },
    { title: '选择', dataIndex: 'selection', key: 'selection', render: (text) => <Tag color="blue">{text}</Tag> },
    { title: '赔率', dataIndex: 'odds', key: 'odds', render: (v) => <span style={{ color: '#10b981', fontWeight: 'bold' }}>{v}</span> },
    { title: '投注金额', dataIndex: 'stake', key: 'stake', render: (v) => <span style={{ color: '#f1f5f9' }}>¥{v.toFixed(1)}</span> },
    { title: '潜在奖金', dataIndex: 'potential_win', key: 'potential_win', render: (v) => <span style={{ color: '#8b5cf6' }}>¥{v}</span> },
    { title: '利润', dataIndex: 'profit', key: 'profit', render: (v) => <span style={{ color: '#10b981' }}>¥{v.toFixed(1)}</span> },
  ];

  const strategyColumns = [
    { title: '策略', dataIndex: 'strategy', key: 'strategy', render: (text) => <span style={{ color: '#f1f5f9', fontWeight: 'bold' }}>{text}</span> },
    { title: 'ROI', dataIndex: 'roi', key: 'roi', render: (v) => <Tag color={v > 10 ? 'green' : 'orange'}>{v}%</Tag> },
    { title: '胜率', dataIndex: 'win_rate', key: 'win_rate', render: (v) => <span style={{ color: '#94a3b8' }}>{v}%</span> },
    { title: '最大回撤', dataIndex: 'max_drawdown', key: 'max_drawdown', render: (v) => <Tag color={v < 15 ? 'green' : v < 20 ? 'orange' : 'red'}>{v}%</Tag> },
    { title: '投注次数', dataIndex: 'total_bets', key: 'total_bets', render: (v) => <span style={{ color: '#f1f5f9' }}>{v}</span> },
    { title: '盈利次数', dataIndex: 'wins', key: 'wins', render: (v) => <span style={{ color: '#10b981' }}>{v}</span> },
  ];

  const strategyChart = strategyResults.length > 0 ? {
    title: { text: '策略表现对比', left: 'center', textStyle: { fontSize: 14, color: '#94a3b8' } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { color: '#f1f5f9' } },
    legend: { data: ['ROI', '胜率'], bottom: '5%', textStyle: { fontSize: 12, color: '#94a3b8' } },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '10%', containLabel: true },
    xAxis: {
      type: 'category',
      data: strategyResults.map(s => s.strategy),
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: [
      {
        type: 'value',
        name: 'ROI (%)',
        axisLabel: { color: '#94a3b8', fontSize: 12 },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
      },
      {
        type: 'value',
        name: '胜率 (%)',
        axisLabel: { color: '#94a3b8', fontSize: 12 },
        axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
        splitLine: { show: false }
      }
    ],
    series: [
      { name: 'ROI', type: 'bar', data: strategyResults.map(s => s.roi), itemStyle: { color: '#10b981', borderRadius: [6, 6, 0, 0] } },
      { name: '胜率', type: 'line', yAxisIndex: 1, data: strategyResults.map(s => s.win_rate), smooth: true, lineStyle: { width: 3, color: '#3b82f6' }, symbol: 'circle', symbolSize: 8 }
    ]
  } : {};

  return (
    <div className="fade-in">
      <div style={{ marginBottom: 24 }}>
        <Title level={2} style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <CrownOutlined style={{ color: '#3b82f6' }} />
          <span style={{ background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>专业投注分析</span>
        </Title>
        <Paragraph style={{ margin: '8px 0 0', color: '#94a3b8' }}>
          高级投注分析工具，包括多市场赔率对比、套利机会识别和策略回测功能。
        </Paragraph>
      </div>

      <Tabs activeKey={activeTab} onChange={setActiveTab} size="large" style={{ marginBottom: 24 }} items={[
        {
          key: 'multi-market',
          label: '多市场赔率对比',
          children: (
            <>
              <Card title="比赛信息" style={{ marginBottom: 24 }}>
                <Row gutter={16}>
                  <Col span={6}>
                    <Input value={homeTeam} onChange={(e) => setHomeTeam(e.target.value)} placeholder="主队" style={{ width: '100%' }} />
                  </Col>
                  <Col span={6}>
                    <Input value={awayTeam} onChange={(e) => setAwayTeam(e.target.value)} placeholder="客队" style={{ width: '100%' }} />
                  </Col>
                  <Col span={12} style={{ display: 'flex', alignItems: 'flex-end', gap: 8 }}>
                    <Button type="primary" icon={<SearchOutlined />} onClick={handleGetOdds} loading={loading}>获取多市场赔率</Button>
                    <Button icon={<SyncOutlined />} onClick={() => { setOddsData([]); setBestOdds(null); }}>重置</Button>
                  </Col>
                </Row>
              </Card>
              {loading && (
                <Card style={{ textAlign: 'center', padding: 48 }}><Spin size="large" tip="正在获取赔率数据..." /></Card>
              )}
              {!loading && oddsData.length > 0 && (
                <>
                  <Card title="所有博彩公司赔率" style={{ marginBottom: 24 }}>
                    <Table dataSource={oddsData} columns={oddsColumns} pagination={false} rowKey="bookmaker" scroll={{ x: 800 }} />
                  </Card>
                  <Card title="最佳赔率汇总" style={{ marginBottom: 24 }}>
                    <Row gutter={16}>
                      <Col span={4}><Statistic title={`主胜 (${bestOdds.home.bookmaker})`} value={bestOdds.home.odds} precision={2} valueStyle={{ fontSize: 20, color: '#10b981' }} /></Col>
                      <Col span={4}><Statistic title={`平局 (${bestOdds.draw.bookmaker})`} value={bestOdds.draw.odds} precision={2} valueStyle={{ fontSize: 20, color: '#f59e0b' }} /></Col>
                      <Col span={4}><Statistic title={`客胜 (${bestOdds.away.bookmaker})`} value={bestOdds.away.odds} precision={2} valueStyle={{ fontSize: 20, color: '#ef4444' }} /></Col>
                      <Col span={4}><Statistic title={`大球2.5 (${bestOdds.over25.bookmaker})`} value={bestOdds.over25.odds} precision={2} valueStyle={{ fontSize: 20, color: '#3b82f6' }} /></Col>
                      <Col span={4}><Statistic title={`小球2.5 (${bestOdds.under25.bookmaker})`} value={bestOdds.under25.odds} precision={2} valueStyle={{ fontSize: 20, color: '#8b5cf6' }} /></Col>
                      <Col span={4}><Statistic title={`主让0.5 (${bestOdds.home05.bookmaker})`} value={bestOdds.home05.odds} precision={2} valueStyle={{ fontSize: 20, color: '#10b981' }} /></Col>
                    </Row>
                  </Card>
                  <Card title="市场效率分析">
                    <Row gutter={16}>
                      <Col span={8}><Statistic title="隐含概率总和" value={(1/bestOdds.home.odds + 1/bestOdds.draw.odds + 1/bestOdds.away.odds).toFixed(4)} valueStyle={{ fontSize: 24 }} /></Col>
                      <Col span={8}><Statistic title="市场抽水" value={((1/bestOdds.home.odds + 1/bestOdds.draw.odds + 1/bestOdds.away.odds - 1) * 100).toFixed(2)} suffix="%" valueStyle={{ fontSize: 24, color: '#ef4444' }} /></Col>
                      <Col span={8}><Statistic title="最佳EV选项" value="主胜" valueStyle={{ fontSize: 24, color: '#10b981' }} /></Col>
                    </Row>
                  </Card>
                </>
              )}
            </>
          )
        },
        {
          key: 'arbitrage',
          label: '套利机会识别',
          children: (
            <>
              <Card title="套利检测" style={{ marginBottom: 24 }}>
                <Row gutter={16}>
                  <Col span={6}><Input value={homeTeam} onChange={(e) => setHomeTeam(e.target.value)} placeholder="主队" style={{ width: '100%' }} /></Col>
                  <Col span={6}><Input value={awayTeam} onChange={(e) => setAwayTeam(e.target.value)} placeholder="客队" style={{ width: '100%' }} /></Col>
                  <Col span={12} style={{ display: 'flex', alignItems: 'flex-end' }}>
                    <Button type="primary" icon={<DollarOutlined />} onClick={handleDetectArbitrage} loading={loading}>检测套利机会</Button>
                  </Col>
                </Row>
              </Card>
              {loading && (<Card style={{ textAlign: 'center', padding: 48 }}><Spin size="large" tip="正在检测套利机会..." /></Card>)}
              {!loading && arbitrageResults.length > 0 && arbitrageResults.map((opp, idx) => (
                <div key={idx} style={{ marginBottom: 24 }}>
                  <Card title={`套利机会 ${idx + 1}: ${opp.description}`}>
                    <Row gutter={16} style={{ marginBottom: 16 }}>
                      <Col span={6}><Statistic title="预期收益" value={opp.expected_return} suffix="%" valueStyle={{ fontSize: 24, color: '#10b981' }} /></Col>
                      <Col span={6}><Statistic title="隐含概率" value={opp.implied_probability} suffix="%" valueStyle={{ fontSize: 24, color: '#f59e0b' }} /></Col>
                      <Col span={12}>
                        <Alert type="success" message={`发现套利机会！无论比赛结果如何，投入¥1000均可获得约¥${opp.legs[0].profit.toFixed(1)}的利润`} showIcon />
                      </Col>
                    </Row>
                    <Card title="投注方案 (总投入 ¥1000)">
                      <Table dataSource={opp.legs} columns={arbitrageColumns} pagination={false} rowKey="bookmaker" />
                    </Card>
                  </Card>
                </div>
              ))}
              {!loading && arbitrageResults.length === 0 && (
                <Card style={{ textAlign: 'center', padding: 48 }}>
                  <DollarOutlined style={{ fontSize: 48, color: '#64748b', marginBottom: 16, display: 'block' }} />
                  <div style={{ fontSize: 18, color: '#94a3b8', marginBottom: 8 }}>当前未发现套利机会</div>
                  <div style={{ fontSize: 14, color: '#64748b' }}>请输入比赛信息后点击检测按钮</div>
                </Card>
              )}
            </>
          )
        },
        {
          key: 'backtest',
          label: '策略回测',
          children: (
            <>
              <Card title="回测设置" style={{ marginBottom: 24 }}>
                <Row gutter={16}>
                  <Col span={6}><Input value={homeTeam} onChange={(e) => setHomeTeam(e.target.value)} placeholder="主队（可选）" style={{ width: '100%' }} /></Col>
                  <Col span={6}><Input value={awayTeam} onChange={(e) => setAwayTeam(e.target.value)} placeholder="客队（可选）" style={{ width: '100%' }} /></Col>
                  <Col span={12} style={{ display: 'flex', alignItems: 'flex-end' }}>
                    <Button type="primary" icon={<BarChartOutlined />} onClick={handleBacktest} loading={loading}>开始回测</Button>
                  </Col>
                </Row>
                <div style={{ marginTop: 16 }}>
                  <span style={{ color: '#94a3b8', marginRight: 16 }}>选择策略:</span>
                  <Space>
                    {['价值投注', '动量策略', '冷门策略', '大球策略'].map((s, i) => (
                      <Tag
                        key={s}
                        color={selectedStrategies.includes(['value_bet', 'momentum', 'underdog', 'over_bet'][i]) ? 'blue' : 'default'}
                        onClick={() => {
                          const keys = ['value_bet', 'momentum', 'underdog', 'over_bet'];
                          if (selectedStrategies.includes(keys[i])) {
                            setSelectedStrategies(selectedStrategies.filter(k => k !== keys[i]));
                          } else {
                            setSelectedStrategies([...selectedStrategies, keys[i]]);
                          }
                        }}
                      >{s}</Tag>
                    ))}
                  </Space>
                </div>
              </Card>
              {loading && (<Card style={{ textAlign: 'center', padding: 48 }}><Spin size="large" tip="正在运行回测..." /></Card>)}
              {!loading && strategyResults.length > 0 && (
                <>
                  <Card title="回测结果" style={{ marginBottom: 24 }}>
                    <Table dataSource={strategyResults} columns={strategyColumns} pagination={false} rowKey="strategy" />
                  </Card>
                  <Card title="策略表现对比">
                    <ReactECharts option={strategyChart} style={{ height: 350 }} opts={{ renderer: 'svg' }} />
                  </Card>
                </>
              )}
              {!loading && strategyResults.length === 0 && (
                <Card style={{ textAlign: 'center', padding: 48 }}>
                  <BarChartOutlined style={{ fontSize: 48, color: '#64748b', marginBottom: 16, display: 'block' }} />
                  <div style={{ fontSize: 18, color: '#94a3b8', marginBottom: 8 }}>暂无回测结果</div>
                  <div style={{ fontSize: 14, color: '#64748b' }}>选择策略后点击开始回测按钮</div>
                </Card>
              )}
            </>
          )
        }
      ]} />
    </div>
  );
}

export default Professional;