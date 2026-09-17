import React, { useState, useEffect } from 'react';
import { Card, Row, Col, Typography, Form, InputNumber, Button, Statistic, Tag, Alert, Progress, Space } from 'antd';
import { WalletOutlined, ArrowUpOutlined, ArrowDownOutlined, LineChartOutlined, BarChartOutlined, SyncOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { fetchApiSafe } from '../utils/api';

const { Title, Paragraph } = Typography;

function Bankroll() {
  const [bankroll, setBankroll] = useState(10000.0);
  const [transactions, setTransactions] = useState([]);
  const [stats, setStats] = useState({
    initialBankroll: 10000.0,
    currentBankroll: 10000.0,
    totalReturn: 0,
    maxDrawdown: 0,
    winRate: 0,
    totalBets: 0,
    profitBets: 0
  });

  useEffect(() => {
    fetchApiSafe('/api/data/bankroll')
      .then(data => {
        if (data.success && data.data) {
          setBankroll(data.data.current || 10000.0);
          setStats({
            initialBankroll: data.data.initial || 10000.0,
            currentBankroll: data.data.current || 10000.0,
            totalReturn: data.data.totalReturn || 0,
            maxDrawdown: data.data.maxDrawdown || 0,
            winRate: data.data.winRate || 0,
            totalBets: data.data.totalBets || 0,
            profitBets: data.data.profitBets || 0
          });
          setTransactions(data.data.transactions || []);
        }
      });
  }, []);

  const handleDeposit = async (values) => {
    const amount = values.deposit;
    const newBankroll = bankroll + amount;
    setBankroll(newBankroll);
    setTransactions([
      { type: 'deposit', amount, date: new Date().toISOString(), balance: newBankroll },
      ...transactions
    ]);
    setStats(prev => ({
      ...prev,
      currentBankroll: newBankroll
    }));
  };

  const handleWithdraw = async (values) => {
    const amount = values.withdraw;
    if (amount > bankroll) {
      return;
    }
    const newBankroll = bankroll - amount;
    setBankroll(newBankroll);
    setTransactions([
      { type: 'withdraw', amount, date: new Date().toISOString(), balance: newBankroll },
      ...transactions
    ]);
    setStats(prev => ({
      ...prev,
      currentBankroll: newBankroll
    }));
  };

  const handleReset = () => {
    setBankroll(10000.0);
    setTransactions([]);
    setStats({
      initialBankroll: 10000.0,
      currentBankroll: 10000.0,
      totalReturn: 0,
      maxDrawdown: 0,
      winRate: 0,
      totalBets: 0,
      profitBets: 0
    });
  };

  const roi = ((bankroll - stats.initialBankroll) / stats.initialBankroll * 100).toFixed(2);
  const profit = (bankroll - stats.initialBankroll).toFixed(2);

  const bankrollChartOption = {
    title: { text: '资金变化趋势', left: 'center', textStyle: { fontSize: 14, color: '#94a3b8' } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '10%', containLabel: true },
    xAxis: {
      type: 'category',
      data: ['第1周', '第2周', '第3周', '第4周', '第5周', '第6周'],
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: {
      type: 'value',
      name: '资金 (¥)',
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      data: [10000, 9500, 10200, 9800, 10800, bankroll],
      type: 'line',
      smooth: true,
      lineStyle: { width: 3, color: '#10b981' },
      areaStyle: { color: '#10b981', opacity: 0.2 },
      symbol: 'circle',
      symbolSize: 8
    }]
  };

  const profitDistributionChart = {
    title: { text: '盈亏分布', left: 'center', textStyle: { fontSize: 14, color: '#94a3b8' } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '10%', containLabel: true },
    xAxis: {
      type: 'category',
      data: ['盈利', '亏损', '打平'],
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: {
      type: 'value',
      name: '次数',
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      data: [stats.profitBets, stats.totalBets - stats.profitBets, 0],
      type: 'bar',
      itemStyle: {
        color: (params) => params.dataIndex === 0 ? '#10b981' : '#ef4444',
        borderRadius: [6, 6, 0, 0]
      }
    }]
  };

  return (
    <div className="fade-in">
      <div style={{ marginBottom: 24 }}>
        <Title level={2} style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <WalletOutlined style={{ color: '#3b82f6' }} />
          <span style={{ background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>资金管理</span>
        </Title>
        <Paragraph style={{ margin: '8px 0 0', color: '#94a3b8' }}>
          管理您的投注资金，跟踪资金变化趋势，实现科学的资金管理策略。
        </Paragraph>
      </div>

      <Card style={{ marginBottom: 24 }}>
        <Row gutter={16}>
          <Col span={12}>
            <Statistic
              title="当前余额"
              value={bankroll}
              precision={2}
              prefix="¥"
              valueStyle={{ fontSize: 48, color: profit >= 0 ? '#10b981' : '#ef4444' }}
              suffix={profit >= 0 ? <LineChartOutlined style={{ color: '#10b981' }} /> : <BarChartOutlined style={{ color: '#ef4444' }} />}
            />
          </Col>
          <Col span={12}>
            <Row gutter={16}>
              <Col span={12}>
                <Statistic
                  title="总收益"
                  value={profit >= 0 ? profit : Math.abs(profit)}
                  precision={2}
                  prefix={`¥${profit >= 0 ? '' : '-'}`}
                  valueStyle={{ fontSize: 24, color: profit >= 0 ? '#10b981' : '#ef4444' }}
                />
              </Col>
              <Col span={12}>
                <Statistic
                  title="收益率"
                  value={Math.abs(roi)}
                  precision={2}
                  suffix="%"
                  prefix={roi >= 0 ? '+' : '-'}
                  valueStyle={{ fontSize: 24, color: roi >= 0 ? '#10b981' : '#ef4444' }}
                />
              </Col>
            </Row>
          </Col>
        </Row>
      </Card>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card hoverable>
            <Statistic title="初始资金" value={stats.initialBankroll} precision={2} prefix="¥" valueStyle={{ fontSize: 20 }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable>
            <Statistic title="总投注次数" value={stats.totalBets} valueStyle={{ fontSize: 20, color: '#3b82f6' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable>
            <Statistic title="胜率" value={stats.winRate} suffix="%" valueStyle={{ fontSize: 20, color: '#8b5cf6' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card hoverable>
            <Statistic title="最大回撤" value={stats.maxDrawdown} suffix="%" valueStyle={{ fontSize: 20, color: '#f59e0b' }} />
          </Card>
        </Col>
      </Row>

      <Card title="资金调整" style={{ marginBottom: 24 }}>
        <Row gutter={16}>
          <Col span={12}>
            <Form layout="vertical" onFinish={handleDeposit}>
              <Form.Item name="deposit" label="存入金额" rules={[{ required: true, min: 0.01 }]}>
                <InputNumber
                  min={0.01}
                  step={100}
                  style={{ width: '100%' }}
                  formatter={(value) => `¥${value}`}
                  defaultValue={1000}
                />
              </Form.Item>
              <Button type="primary" icon={<ArrowUpOutlined />} htmlType="submit">
                存入资金
              </Button>
            </Form>
          </Col>
          <Col span={12}>
            <Form layout="vertical" onFinish={handleWithdraw}>
              <Form.Item name="withdraw" label="提取金额" rules={[{ required: true, min: 0.01, max: bankroll }]}>
                <InputNumber
                  min={0.01}
                  max={bankroll}
                  step={100}
                  style={{ width: '100%' }}
                  formatter={(value) => `¥${value}`}
                  defaultValue={1000}
                />
              </Form.Item>
              <Button type="danger" icon={<ArrowDownOutlined />} htmlType="submit">
                提取资金
              </Button>
            </Form>
          </Col>
        </Row>
        <div style={{ marginTop: 16, textAlign: 'right' }}>
          <Button icon={<SyncOutlined />} onClick={handleReset}>
            重置资金
          </Button>
        </div>
      </Card>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={12}>
          <Card title="资金变化趋势">
            <ReactECharts option={bankrollChartOption} style={{ height: 300 }} opts={{ renderer: 'svg' }} />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="盈亏分布">
            <ReactECharts option={profitDistributionChart} style={{ height: 300 }} opts={{ renderer: 'svg' }} />
          </Card>
        </Col>
      </Row>

      <Card title="资金安全提示">
        <Row gutter={16}>
          <Col span={12}>
            <Alert
              type="warning"
              message="凯利准则建议"
              description="根据凯利公式，单笔投注金额不应超过资金的5%，以控制风险"
              showIcon
              style={{ marginBottom: 16 }}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ color: '#94a3b8' }}>建议单笔投注上限:</span>
              <Tag color="blue" style={{ fontSize: 16, padding: '4px 12px' }}>
                ¥{(bankroll * 0.05).toFixed(2)}
              </Tag>
            </div>
          </Col>
          <Col span={12}>
            <Alert
              type="info"
              message="回撤监控"
              description={`当前资金回撤率: ${((stats.initialBankroll - bankroll) / stats.initialBankroll * 100).toFixed(1)}%`}
              showIcon
            />
            <Progress
              percent={Math.min(((bankroll / stats.initialBankroll) * 100), 100)}
              strokeColor={{
                '0%': '#10b981',
                '100%': '#ef4444'
              }}
              style={{ marginTop: 16 }}
            />
          </Col>
        </Row>
      </Card>

      {transactions.length > 0 && (
        <Card title="交易记录" style={{ marginTop: 24 }}>
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            {transactions.slice(0, 10).map((t, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                <div>
                  <span style={{ color: '#94a3b8', marginRight: 8 }}>{new Date(t.date).toLocaleString()}</span>
                  <Tag color={t.type === 'deposit' ? 'green' : 'red'}>
                    {t.type === 'deposit' ? '存入' : '提取'}
                  </Tag>
                </div>
                <div>
                  <span style={{ color: t.type === 'deposit' ? '#10b981' : '#ef4444', marginRight: 16 }}>
                    {t.type === 'deposit' ? '+' : '-'}¥{t.amount.toFixed(2)}
                  </span>
                  <span style={{ color: '#64748b' }}>余额: ¥{t.balance.toFixed(2)}</span>
                </div>
              </div>
            ))}
          </Space>
        </Card>
      )}
    </div>
  );
}

export default Bankroll;