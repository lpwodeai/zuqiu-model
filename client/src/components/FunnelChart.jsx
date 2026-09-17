import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';

function FunnelChart({ data, title, height = 300, colors = null }) {
  const defaultColors = ['#1890ff', '#52c41a', '#faad14', '#ff7a45', '#ff4d4f', '#722ed1'];
  
  const option = useMemo(() => ({
    title: {
      text: title,
      left: 'center',
      textStyle: { fontSize: 16, fontWeight: 'bold' }
    },
    tooltip: {
      trigger: 'item',
      formatter: (params) => {
        const percent = ((params.value / data[0]?.value) * 100).toFixed(1);
        return `${params.name}<br/>数值: ${params.value}<br/>转化率: ${percent}%`;
      }
    },
    legend: {
      data: data.map(item => item.name),
      bottom: 0,
      textStyle: { fontSize: 12 }
    },
    series: [{
      name: title,
      type: 'funnel',
      left: '10%',
      top: '10%',
      bottom: '15%',
      width: '80%',
      min: 0,
      max: data[0]?.value || 100,
      minSize: '0%',
      maxSize: '100%',
      sort: 'descending',
      gap: 2,
      label: {
        show: true,
        position: 'inside',
        formatter: (params) => {
          const percent = ((params.value / data[0]?.value) * 100).toFixed(0);
          return `${params.name}\n${params.value}\n(${percent}%)`;
        },
        fontSize: 12,
        fontWeight: 'bold',
        color: '#fff',
        lineHeight: 18
      },
      labelLine: {
        length: 10,
        lineStyle: { width: 1, type: 'solid' }
      },
      itemStyle: {
        borderColor: '#fff',
        borderWidth: 1,
        shadowBlur: 10,
        shadowColor: 'rgba(0,0,0,0.2)'
      },
      emphasis: {
        label: { fontSize: 14 },
        itemStyle: { shadowBlur: 20, shadowColor: 'rgba(0,0,0,0.3)' }
      },
      data: data.map((item, index) => ({
        ...item,
        itemStyle: { color: colors?.[index] || defaultColors[index % defaultColors.length] }
      }))
    }]
  }), [data, title, colors]);

  return (
    <ReactECharts 
      option={option} 
      style={{ height, width: '100%' }}
      opts={{ renderer: 'svg' }}
    />
  );
}

export default FunnelChart;