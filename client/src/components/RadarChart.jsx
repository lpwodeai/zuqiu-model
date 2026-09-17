import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';

function RadarChart({ data, title, height = 300 }) {
  const indicators = data.length > 0 
    ? Object.keys(data[0]).filter(key => key !== 'name' && key !== 'color').map(key => ({
        name: key,
        max: Math.max(...data.map(item => item[key])) * 1.2
      }))
    : [];

  const option = useMemo(() => ({
    title: {
      text: title,
      left: 'center',
      textStyle: { fontSize: 16, fontWeight: 'bold' }
    },
    tooltip: {
      trigger: 'item',
      formatter: (params) => {
        let result = `<strong>${params.name}</strong><br/>`;
        params.value.forEach((val, idx) => {
          result += `${indicators[idx]?.name}: ${val.toFixed(1)}<br/>`;
        });
        return result;
      }
    },
    legend: {
      data: data.map(item => item.name),
      bottom: 0,
      textStyle: { fontSize: 12 }
    },
    radar: {
      indicator: indicators,
      center: ['50%', '50%'],
      radius: '65%',
      startAngle: 90,
      splitNumber: 5,
      shape: 'polygon',
      axisName: {
        color: '#666',
        fontSize: 12
      },
      splitLine: { lineStyle: { color: ['#eee', '#ddd', '#ccc', '#bbb', '#aaa'] } },
      splitArea: { show: true, areaStyle: { color: ['rgba(24, 144, 255, 0.05)', 'rgba(24, 144, 255, 0.1)'] } },
      axisLine: { lineStyle: { color: '#ccc' } }
    },
    series: [{
      type: 'radar',
      emphasis: { lineStyle: { width: 3 } },
      data: data.map(item => ({
        value: indicators.map(ind => item[ind.name] || 0),
        name: item.name,
        lineStyle: { width: 2, color: item.color || '#1890ff' },
        areaStyle: { color: item.color || '#1890ff', opacity: 0.2 },
        itemStyle: { color: item.color || '#1890ff' }
      }))
    }]
  }), [data, title, indicators]);

  return (
    <ReactECharts 
      option={option} 
      style={{ height, width: '100%' }}
      opts={{ renderer: 'svg' }}
    />
  );
}

export default RadarChart;