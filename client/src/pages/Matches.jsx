/**
 * 比赛页面组件
 */

import React, { useState, useEffect } from 'react';
import { 
  Card, Table, Tag, Typography, Row, Col, Statistic, 
  Tabs, Select, DatePicker, Button, Space, Badge
} from 'antd';
import { CalendarOutlined, TrophyOutlined, ClockCircleOutlined } from '@ant-design/icons';
import { fetchApiSafe } from '../utils/api';

const { Title } = Typography;

function Matches() {
  const [matches, setMatches] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState({});
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 });
  const [stats, setStats] = useState({ total: 0, completed: 0, live: 0, pending: 0 });

  useEffect(() => {
    fetchStats();
    setPagination(prev => ({ ...prev, current: 1 }));
    fetchMatches();
  }, [filters]);

  useEffect(() => {
    fetchMatches();
  }, [pagination.current]);

  const fetchMatches = async () => {
    setLoading(true);
    const queryParams = new URLSearchParams();
    if (filters.league) queryParams.append('league', filters.league);
    if (filters.status) queryParams.append('status', filters.status);
    if (filters.season) queryParams.append('season', filters.season);
    queryParams.append('page', pagination.current);
    queryParams.append('limit', pagination.pageSize);
    
    try {
      const data = await fetchApiSafe(`/api/data/matches?${queryParams.toString()}`);
      if (data.success) {
        setMatches(data.data);
        setPagination(prev => ({ ...prev, total: data.total }));
      }
    } finally {
      setLoading(false);
    }
  };

  const fetchStats = async () => {
    const queryParams = new URLSearchParams();
    if (filters.league) queryParams.append('league', filters.league);
    if (filters.season) queryParams.append('season', filters.season);
    
    try {
      const data = await fetchApiSafe(`/api/data/matches/stats?${queryParams.toString()}`);
      if (data.success) {
        setStats(data.data);
      }
    } catch (err) {
      console.error('获取统计失败:', err);
    }
  };

  const handleTableChange = (newPagination) => {
    setPagination(newPagination);
  };

  const columns = [
    { 
      title: '比赛', 
      key: 'match',
      render: (_, record) => (
        <Space>
          <Tag color="blue">{record.homeTeam}</Tag>
          <span>vs</span>
          <Tag color="red">{record.awayTeam}</Tag>
        </Space>
      )
    },
    { title: '联赛', dataIndex: 'league', key: 'league', render: (l) => <Tag color="purple">{l}</Tag> },
    { title: '轮次', dataIndex: 'round', key: 'round', render: (r) => <span style={{ color: '#94a3b8' }}>{r}轮</span> },
    { 
      title: '日期', 
      dataIndex: 'date', 
      key: 'date',
      render: (date) => <Space><ClockCircleOutlined />{date}</Space>
    },
    { 
      title: '状态', 
      dataIndex: 'status', 
      key: 'status',
      render: (status) => {
        const color = status === 'completed' ? 'green' : status === 'live' ? 'orange' : 'default';
        const text = status === 'completed' ? '已完成' : status === 'live' ? '进行中' : '待开始';
        return <Badge status={color} text={text} />;
      }
    },
    { 
      title: '比分', 
      key: 'score',
      render: (_, record) => {
        if (record.status === 'completed' && record.result) {
          return <Tag color="green">{record.result.homeGoals}:{record.result.awayGoals}</Tag>;
        }
        return '-';
      }
    },
    {
      title: '操作',
      key: 'action',
      render: (_, record) => (
        <Button type="link">预测</Button>
      )
    }
  ];

  return (
    <div>
      <Title level={2}>
        <CalendarOutlined /> 比赛日程
      </Title>

      {/* 筛选 */}
      <Card style={{ marginBottom: 24 }}>
        <Space>
          <Select style={{ width: 150 }} placeholder="选择联赛" allowClear onChange={(v) => setFilters({...filters, league: v})}>
            <Select.Option value="PL">英超</Select.Option>
            <Select.Option value="SA">西甲</Select.Option>
            <Select.Option value="BL1">德甲</Select.Option>
            <Select.Option value="SerieA">意甲</Select.Option>
            <Select.Option value="FL1">法甲</Select.Option>
          </Select>
          <Select style={{ width: 150 }} placeholder="比赛状态" allowClear onChange={(v) => setFilters({...filters, status: v})}>
            <Select.Option value="pending">待开始</Select.Option>
            <Select.Option value="live">进行中</Select.Option>
            <Select.Option value="completed">已完成</Select.Option>
          </Select>
          <Select style={{ width: 150 }} placeholder="选择赛季" allowClear onChange={(v) => setFilters({...filters, season: v})}>
            <Select.Option value="2025">2025-2026赛季</Select.Option>
            <Select.Option value="2024">2024-2025赛季</Select.Option>
          </Select>
        </Space>
      </Card>

      {/* 统计 */}
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic title="总比赛" value={stats.total} prefix={<TrophyOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="已完成" value={stats.completed} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="进行中" value={stats.live} valueStyle={{ color: '#faad14' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="待进行" value={stats.pending} />
          </Card>
        </Col>
      </Row>

      {/* 比赛列表 */}
      <Table 
        dataSource={matches} 
        columns={columns} 
        loading={loading} 
        pagination={{ 
          current: pagination.current,
          pageSize: pagination.pageSize,
          total: pagination.total,
          showSizeChanger: true,
          showQuickJumper: true,
          showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条，共 ${total} 条`
        }} 
        onChange={handleTableChange}
        rowKey="id" 
      />
    </div>
  );
}

export default Matches;