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
import { 
  TrophyOutlined, 
  StarOutlined, 
  AlertOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined
} from '@ant-design/icons';
import axios from 'axios';

const LeagueRules = () => {
  const [leagues, setLeagues] = useState([]);
  const [validationReport, setValidationReport] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [leaguesRes, reportRes] = await Promise.all([
        axios.get('/api/leagues'),
        axios.get('/api/leagues/validation/report')
      ]);
      setLeagues(leaguesRes.data.data);
      setValidationReport(reportRes.data.data);
    } catch (error) {
      console.error('获取联赛数据失败:', error);
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case '通过': return 'green';
      case '警告': return 'orange';
      case '需调整': return 'red';
      default: return 'default';
    }
  };

  const getCheckIcon = (passed) => {
    return passed ? (
      <CheckCircleOutlined style={{ color: '#10b981' }} />
    ) : (
      <CloseCircleOutlined style={{ color: '#ef4444' }} />
    );
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '400px' }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className="fade-in">
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ margin: 0, background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>五大联赛规则适配校验</h2>
      </div>
      
      <Tabs defaultActiveKey="summary" size="large" items={[
        {
          key: 'summary',
          label: '校验概览',
          children: validationReport && (
            <>
              <Row gutter={16} style={{ marginBottom: 24 }}>
                <Col span={6}>
                  <Card hoverable>
                    <Statistic title="覆盖联赛" value={validationReport.leagues} prefix={<TrophyOutlined />} valueStyle={{ fontSize: 28 }} />
                  </Card>
                </Col>
                <Col span={6}>
                  <Card hoverable>
                    <Statistic title="总检查项" value={validationReport.totalChecks} prefix={<StarOutlined />} valueStyle={{ fontSize: 28 }} />
                  </Card>
                </Col>
                <Col span={6}>
                  <Card hoverable>
                    <Statistic title="通过项" value={validationReport.passedChecks} prefix={<CheckCircleOutlined />} valueStyle={{ fontSize: 28, color: '#10b981' }} />
                  </Card>
                </Col>
                <Col span={6}>
                  <Card hoverable>
                    <Statistic title="总体通过率" value={validationReport.overallPassRate} suffix="%" prefix={<AlertOutlined />} valueStyle={{ fontSize: 28, color: validationReport.overallPassRate >= 90 ? '#10b981' : '#f59e0b' }} />
                  </Card>
                </Col>
              </Row>
              <Card title="各联赛校验状态">
                <Table
                  dataSource={Object.entries(validationReport.summary).map(([id, item]) => ({ key: id, ...item }))}
                  columns={[
                    { title: '联赛', dataIndex: 'name', key: 'name', render: (text) => <span style={{ color: '#f1f5f9' }}>{text}</span> },
                    { 
                      title: '通过率', 
                      dataIndex: 'passRate', 
                      key: 'passRate',
                      render: (rate) => (
                        <div>
                          <Progress percent={parseFloat(rate)} size="small" strokeColor="#3b82f6" />
                          <span style={{ marginLeft: 8, color: '#94a3b8' }}>{rate}%</span>
                        </div>
                      )
                    },
                    { 
                      title: '问题数', 
                      dataIndex: 'issuesCount', 
                      key: 'issuesCount',
                      render: (count) => <Tag color={count === 0 ? 'green' : count <= 2 ? 'orange' : 'red'}>{count}个</Tag>
                    },
                    { 
                      title: '状态', 
                      dataIndex: 'status', 
                      key: 'status',
                      render: (status) => <Tag color={getStatusColor(status)}>{status}</Tag>
                    }
                  ]}
                />
              </Card>
            </>
          )
        },
        {
          key: 'details',
          label: '规则详情',
          children: (
            <>
              <Card title="积分制度对比">
                <Table
                  dataSource={leagues}
                  columns={[
                    { title: '联赛', dataIndex: 'name', key: 'name', render: (text) => <span style={{ color: '#f1f5f9' }}>{text}</span> },
                    { title: '球队数', dataIndex: 'teams', key: 'teams', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '单队场次', dataIndex: 'matchesPerTeam', key: 'matchesPerTeam', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '总场次', dataIndex: 'totalMatches', key: 'totalMatches', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '胜场分', dataIndex: ['pointsSystem', 'win'], key: 'win', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '平局分', dataIndex: ['pointsSystem', 'draw'], key: 'draw', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '负场分', dataIndex: ['pointsSystem', 'loss'], key: 'loss', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> }
                  ]}
                />
              </Card>
              <Card title="升降级规则" style={{ marginTop: 24 }}>
                <Table
                  dataSource={leagues}
                  columns={[
                    { title: '联赛', dataIndex: 'name', key: 'name', render: (text) => <span style={{ color: '#f1f5f9' }}>{text}</span> },
                    { title: '直接升级', dataIndex: ['promotionRelegation', 'promotion'], key: 'promotion', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '直接降级', dataIndex: ['promotionRelegation', 'relegation'], key: 'relegation', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '附加赛球队', dataIndex: ['promotionRelegation', 'playoffTeams'], key: 'playoffTeams', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '附加赛胜者', dataIndex: ['promotionRelegation', 'playoffWinners'], key: 'playoffWinners', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> }
                  ]}
                />
              </Card>
              <Card title="欧战资格" style={{ marginTop: 24 }}>
                <Table
                  dataSource={leagues}
                  columns={[
                    { title: '联赛', dataIndex: 'name', key: 'name', render: (text) => <span style={{ color: '#f1f5f9' }}>{text}</span> },
                    { title: '欧冠', dataIndex: ['europeanQualification', 'championsLeague'], key: 'championsLeague', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '欧联', dataIndex: ['europeanQualification', 'europaLeague'], key: 'europaLeague', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '欧协联', dataIndex: ['europeanQualification', 'europaConferenceLeague'], key: 'europaConferenceLeague', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> }
                  ]}
                />
              </Card>
            </>
          )
        },
        {
          key: 'special',
          label: '特殊规则',
          children: (
            <>
              <Card title="VAR与技术规则">
                <Table
                  dataSource={leagues}
                  columns={[
                    { title: '联赛', dataIndex: 'name', key: 'name', render: (text) => <span style={{ color: '#f1f5f9' }}>{text}</span> },
                    { title: 'VAR使用', dataIndex: ['specialRules', 'varUsage'], key: 'varUsage', render: (val) => getCheckIcon(val) },
                    { title: '门线技术', dataIndex: ['specialRules', 'goalLineTechnology'], key: 'goalLineTechnology', render: (val) => getCheckIcon(val) },
                    { title: '第五换人', dataIndex: ['specialRules', 'fifthSubstitute', 'enabled'], key: 'fifthSubstitute', render: (val) => getCheckIcon(val) },
                    { title: '水停', dataIndex: ['specialRules', 'waterBreak', 'enabled'], key: 'waterBreak', render: (val) => getCheckIcon(val) },
                    { title: '红牌停赛', dataIndex: ['specialRules', 'redCardBan', 'directRed'], key: 'redCard', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> }
                  ]}
                />
              </Card>
              <Card title="天气影响因素" style={{ marginTop: 24 }}>
                <Table
                  dataSource={leagues}
                  columns={[
                    { title: '联赛', dataIndex: 'name', key: 'name', render: (text) => <span style={{ color: '#f1f5f9' }}>{text}</span> },
                    { title: '雪战', dataIndex: ['weatherImpact', 'snowGames'], key: 'snowGames', render: (val) => getCheckIcon(val) },
                    { title: '雨战影响', dataIndex: ['weatherImpact', 'rainImpact'], key: 'rainImpact', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                    { title: '雾天延迟', dataIndex: ['weatherImpact', 'fogDelay'], key: 'fogDelay', render: (val) => getCheckIcon(val) },
                    { 
                      title: '温度范围', 
                      key: 'tempRange',
                      render: (_, record) => <span style={{ color: '#94a3b8' }}>{record.weatherImpact.temperatureRange[0]}~{record.weatherImpact.temperatureRange[1]}°C</span>
                    }
                  ]}
                />
              </Card>
            </>
          )
        },
        {
          key: 'validation',
          label: '校验详情',
          children: validationReport && (
            <div>
              {Object.entries(validationReport.details).map(([leagueId, details]) => (
                <Card key={leagueId} title={<span style={{ color: '#f1f5f9' }}>{details.league}</span>} style={{ marginBottom: 24 }}>
                  <Row gutter={16}>
                    <Col span={6}>
                      <Statistic title="通过率" value={details.passRate} suffix="%" valueStyle={{ fontSize: 28, color: parseFloat(details.passRate) >= 90 ? '#10b981' : '#f59e0b' }} />
                    </Col>
                    <Col span={6}>
                      <Statistic title="问题数" value={details.issues.length} prefix={details.issues.length > 0 ? <AlertOutlined /> : <CheckCircleOutlined />} valueStyle={{ fontSize: 28, color: details.issues.length > 0 ? '#ef4444' : '#10b981' }} />
                    </Col>
                  </Row>
                  {details.issues.length > 0 && (
                    <div style={{ marginTop: 16 }}>
                      <h4 style={{ color: '#ef4444' }}>问题列表:</h4>
                      <ul>{details.issues.map((issue, index) => <li key={index}><AlertOutlined style={{ color: '#ef4444' }} /> {issue}</li>)}</ul>
                    </div>
                  )}
                  <div style={{ marginTop: 16 }}>
                    <h4 style={{ color: '#f1f5f9' }}>校验项详情:</h4>
                    <Table
                      dataSource={details.checks}
                      pagination={false}
                      columns={[
                        { title: '分类', dataIndex: 'category', key: 'category', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                        { title: '检查项', dataIndex: 'check', key: 'check', render: (text) => <span style={{ color: '#f1f5f9' }}>{text}</span> },
                        { title: '期望', dataIndex: 'expected', key: 'expected', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                        { title: '实际', dataIndex: 'actual', key: 'actual', render: (text) => <span style={{ color: '#94a3b8' }}>{text}</span> },
                        { title: '结果', key: 'passed', render: (_, record) => getCheckIcon(record.passed) }
                      ]}
                    />
                  </div>
                </Card>
              ))}
            </div>
          )
        }
      ]} />
    </div>
  );
};

export default LeagueRules;