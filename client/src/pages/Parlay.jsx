import React, { useState } from 'react';
import { Card, Row, Col, Typography, Form, Input, InputNumber, Button, Statistic, Tag, Table, Space, Alert } from 'antd';
import { LinkOutlined, PlusOutlined, DeleteOutlined, CalculatorOutlined, SyncOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';

const { Title, Paragraph } = Typography;

function Parlay() {
  const [selections, setSelections] = useState([]);
  const [stake, setStake] = useState(100);
  const [form] = Form.useForm();

  const handleAddSelection = async () => {
    try {
      const values = await form.validateFields();
      const newSelection = {
        id: Date.now(),
        match: values.match,
        odds: values.odds,
        type: values.type,
        probability: (1 / values.odds * 100).toFixed(1)
      };
      setSelections([...selections, newSelection]);
      form.resetFields();
    } catch (err) {
      console.error('添加串关项失败:', err);
    }
  };

  const handleRemoveSelection = (id) => {
    setSelections(selections.filter(s => s.id !== id));
  };

  const handleClear = () => {
    setSelections([]);
    setStake(100);
  };

  const totalOdds = selections.length > 0 
    ? selections.reduce((acc, s) => acc * s.odds, 1).toFixed(2)
    : 1.00;

  const totalProbability = selections.length > 0
    ? (selections.reduce((acc, s) => acc * (1 / s.odds), 1) * 100).toFixed(2)
    : 0;

  const potentialWin = (totalOdds * stake).toFixed(2);
  const profit = (potentialWin - stake).toFixed(2);

  const columns = [
    { title: '比赛', dataIndex: 'match', key: 'match', render: (text) => <span style={{ color: '#f1f5f9', fontWeight: 'bold' }}>{text}</span> },
    { title: '类型', dataIndex: 'type', key: 'type', render: (text) => <Tag color="blue">{text}</Tag> },
    { title: '赔率', dataIndex: 'odds', key: 'odds', render: (v) => <span style={{ color: '#10b981', fontSize: 16, fontWeight: 'bold' }}>{v}</span> },
    { title: '概率', dataIndex: 'probability', key: 'probability', render: (v) => <span style={{ color: '#94a3b8' }}>{v}%</span> },
    { title: '操作', key: 'action', render: (_, record) => (
      <Button type="text" danger icon={<DeleteOutlined />} onClick={() => handleRemoveSelection(record.id)}>
        删除
      </Button>
    )}
  ];

  const oddsDistributionChart = selections.length > 0 ? {
    title: { text: '赔率分布', left: 'center', textStyle: { fontSize: 14, color: '#94a3b8' } },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '10%', containLabel: true },
    xAxis: {
      type: 'category',
      data: selections.map((s, i) => `选项${i + 1}`),
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: {
      type: 'value',
      name: '赔率',
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      data: selections.map(s => s.odds),
      type: 'bar',
      itemStyle: {
        color: 'linear-gradient(135deg, #3b82f6, #8b5cf6)',
        borderRadius: [6, 6, 0, 0]
      }
    }]
  } : {};

  const profitChart = {
    title: { text: '不同投注金额收益', left: 'center', textStyle: { fontSize: 14, color: '#94a3b8' } },
    tooltip: { trigger: 'axis', textStyle: { color: '#f1f5f9' } },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '10%', containLabel: true },
    xAxis: {
      type: 'category',
      data: ['¥50', '¥100', '¥200', '¥500', '¥1000'],
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } }
    },
    yAxis: {
      type: 'value',
      name: '收益 (¥)',
      axisLabel: { color: '#94a3b8', fontSize: 12 },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.1)' } },
      splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
    },
    series: [{
      data: [50 * (totalOdds - 1), 100 * (totalOdds - 1), 200 * (totalOdds - 1), 500 * (totalOdds - 1), 1000 * (totalOdds - 1)],
      type: 'line',
      smooth: true,
      lineStyle: { width: 3, color: '#10b981' },
      areaStyle: { color: '#10b981', opacity: 0.2 },
      symbol: 'circle',
      symbolSize: 8
    }]
  };

  return (
    <div className="fade-in">
      <div style={{ marginBottom: 24 }}>
        <Title level={2} style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <LinkOutlined style={{ color: '#3b82f6' }} />
          <span style={{ background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>串关计算器</span>
        </Title>
        <Paragraph style={{ margin: '8px 0 0', color: '#94a3b8' }}>
          计算多场比赛串关投注的赔率和潜在收益，支持2串1、3串1、N串1等多种串关方式。
        </Paragraph>
      </div>

      <Card title="添加串关项" style={{ marginBottom: 24 }}>
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={6}>
              <Form.Item name="match" label="比赛" rules={[{ required: true }]}>
                <Input placeholder="例如: 曼城 vs 利物浦" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="type" label="类型" rules={[{ required: true }]}>
                <Input placeholder="例如: 主胜" />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="odds" label="赔率" rules={[{ required: true, min: 1.01 }]}>
                <InputNumber
                  min={1.01}
                  max={100}
                  step={0.01}
                  style={{ width: '100%' }}
                  formatter={(value) => value.toFixed(2)}
                  defaultValue={2.0}
                />
              </Form.Item>
            </Col>
            <Col span={6} style={{ display: 'flex', alignItems: 'flex-end' }}>
              <Button type="primary" icon={<PlusOutlined />} onClick={handleAddSelection} size="large">
                添加
              </Button>
            </Col>
          </Row>
        </Form>
      </Card>

      {selections.length > 0 && (
        <div>
          <Card title={`当前串关 (${selections.length}串1)`} style={{ marginBottom: 24 }}>
            <Table
              dataSource={selections}
              columns={columns}
              pagination={false}
              rowKey="id"
            />
            <div style={{ marginTop: 16, textAlign: 'right' }}>
              <Button icon={<SyncOutlined />} onClick={handleClear}>
                清空串关
              </Button>
            </div>
          </Card>

          <Card title="串关计算结果" style={{ marginBottom: 24 }}>
            <Row gutter={16}>
              <Col span={6}>
                <Statistic
                  title="串关赔率"
                  value={totalOdds}
                  precision={2}
                  valueStyle={{ fontSize: 32, color: '#10b981' }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="理论概率"
                  value={totalProbability}
                  precision={2}
                  suffix="%"
                  valueStyle={{ fontSize: 32, color: '#f59e0b' }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="投注金额"
                  value={stake}
                  precision={0}
                  prefix="¥"
                  valueStyle={{ fontSize: 32 }}
                />
              </Col>
              <Col span={6}>
                <Statistic
                  title="潜在奖金"
                  value={potentialWin}
                  precision={2}
                  prefix="¥"
                  valueStyle={{ fontSize: 32, color: '#8b5cf6' }}
                />
              </Col>
            </Row>

            <Row gutter={16} style={{ marginTop: 16 }}>
              <Col span={12}>
                <div style={{ background: 'rgba(16, 185, 129, 0.1)', borderRadius: 8, padding: 16, textAlign: 'center' }}>
                  <div style={{ fontSize: 14, color: '#94a3b8', marginBottom: 4 }}>预计盈利</div>
                  <div style={{ fontSize: 28, color: '#10b981', fontWeight: 'bold' }}>¥{profit}</div>
                  <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>回报率 {(totalOdds - 1) * 100}%</div>
                </div>
              </Col>
              <Col span={12}>
                <Form layout="vertical">
                  <Form.Item label="调整投注金额">
                    <InputNumber
                      value={stake}
                      onChange={setStake}
                      min={1}
                      max={100000}
                      step={100}
                      style={{ width: '100%' }}
                      formatter={(value) => `¥${value}`}
                    />
                  </Form.Item>
                  <Button type="primary" icon={<CalculatorOutlined />} block size="large">
                    计算收益
                  </Button>
                </Form>
              </Col>
            </Row>
          </Card>

          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={12}>
              <Card title="赔率分布">
                <ReactECharts option={oddsDistributionChart} style={{ height: 250 }} opts={{ renderer: 'svg' }} />
              </Card>
            </Col>
            <Col span={12}>
              <Card title="收益曲线">
                <ReactECharts option={profitChart} style={{ height: 250 }} opts={{ renderer: 'svg' }} />
              </Card>
            </Col>
          </Row>

          <Card title="串关风险提示">
            <Alert
              type="warning"
              message="串关投注风险提示"
              description={`${selections.length}串1投注需要所有选项全部命中才能获得奖金，理论概率为${totalProbability}%，请谨慎投注`}
              showIcon
              style={{ marginBottom: 16 }}
            />
            <Row gutter={16}>
              <Col span={8}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 12, color: '#94a3b8' }}>单场胜率要求</div>
                  <div style={{ fontSize: 24, color: '#f59e0b', fontWeight: 'bold' }}>{(Math.pow(totalProbability / 100, 1 / selections.length) * 100).toFixed(1)}%</div>
                </div>
              </Col>
              <Col span={8}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 12, color: '#94a3b8' }}>风险等级</div>
                  <Tag color={selections.length > 3 ? 'red' : selections.length > 2 ? 'orange' : 'blue'} style={{ fontSize: 16, padding: '4px 16px' }}>
                    {selections.length > 3 ? '高风险' : selections.length > 2 ? '中风险' : '低风险'}
                  </Tag>
                </div>
              </Col>
              <Col span={8}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 12, color: '#94a3b8' }}>建议资金占比</div>
                  <div style={{ fontSize: 24, color: '#10b981', fontWeight: 'bold' }}>{(5 / selections.length).toFixed(1)}%</div>
                </div>
              </Col>
            </Row>
          </Card>
        </div>
      )}

      {selections.length === 0 && (
        <Card style={{ textAlign: 'center', padding: 48 }}>
          <LinkOutlined style={{ fontSize: 48, color: '#64748b', marginBottom: 16, display: 'block' }} />
          <div style={{ fontSize: 18, color: '#94a3b8', marginBottom: 8 }}>暂无串关项</div>
          <div style={{ fontSize: 14, color: '#64748b' }}>请在上方添加比赛选项开始计算</div>
        </Card>
      )}
    </div>
  );
}

export default Parlay;