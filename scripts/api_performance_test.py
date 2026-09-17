import requests
import time
import json
import statistics
from datetime import datetime

BASE_URL = "http://localhost:3000"

def measure_response_time(url, method='GET', data=None, headers=None, retries=3):
    times = []
    success_count = 0
    
    for i in range(retries):
        try:
            start_time = time.time()
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=10)
            elapsed = (time.time() - start_time) * 1000
            
            if response.status_code == 200:
                times.append(elapsed)
                success_count += 1
                print(f"  请求 {i+1}: {elapsed:.2f}ms, Status: {response.status_code}")
            else:
                print(f"  请求 {i+1}: FAILED, Status: {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"  请求 {i+1}: ERROR - {e}")
    
    if times:
        return {
            'success_count': success_count,
            'total_count': retries,
            'avg_time': statistics.mean(times),
            'min_time': min(times),
            'max_time': max(times),
            'p95_time': sorted(times)[int(len(times) * 0.95)] if len(times) >= 20 else max(times),
            'times': times
        }
    return None

def test_api_endpoints():
    print("="*70)
    print("API性能测试报告")
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    results = {}
    
    print("\n1. 基础健康检查")
    print("-"*50)
    results['health'] = measure_response_time(f"{BASE_URL}/api/health")
    
    print("\n2. 球队列表")
    print("-"*50)
    results['teams'] = measure_response_time(f"{BASE_URL}/api/teams")
    
    print("\n3. 联赛分组")
    print("-"*50)
    results['teams_groups'] = measure_response_time(f"{BASE_URL}/api/teams/groups")
    
    print("\n4. 支持的球队(预测API)")
    print("-"*50)
    results['predict_teams'] = measure_response_time(f"{BASE_URL}/api/predict/teams")
    
    print("\n5. 比赛列表")
    print("-"*50)
    results['matches'] = measure_response_time(f"{BASE_URL}/api/data/matches?limit=20")
    
    print("\n6. 比赛统计")
    print("-"*50)
    results['matches_stats'] = measure_response_time(f"{BASE_URL}/api/data/matches/stats")
    
    print("\n7. 分析数据")
    print("-"*50)
    results['analysis'] = measure_response_time(f"{BASE_URL}/api/data/analysis")
    
    print("\n8. 数据状态")
    print("-"*50)
    results['status'] = measure_response_time(f"{BASE_URL}/api/data/status")
    
    print("\n9. 预测API (POST)")
    print("-"*50)
    predict_data = {
        "homeTeam": "pl_mci",
        "awayTeam": "pl_liv",
        "options": {
            "venue": "sea_level",
            "neutral": False
        }
    }
    results['predict'] = measure_response_time(
        f"{BASE_URL}/api/predict",
        method='POST',
        data=predict_data
    )
    
    print("\n10. 融合赔率预测 (POST)")
    print("-"*50)
    predict_with_odds_data = {
        "homeTeam": "pl_mci",
        "awayTeam": "pl_liv",
        "odds": {"win": 1.85, "draw": 3.40, "lose": 4.20},
        "options": {"venue": "sea_level", "neutral": False}
    }
    results['predict_with_odds'] = measure_response_time(
        f"{BASE_URL}/api/predict/with-odds",
        method='POST',
        data=predict_with_odds_data
    )
    
    print("\n11. 联赛列表")
    print("-"*50)
    results['leagues'] = measure_response_time(f"{BASE_URL}/api/leagues")
    
    print("\n12. 赔率数据")
    print("-"*50)
    results['odds'] = measure_response_time(f"{BASE_URL}/api/odds")
    
    return results

def generate_report(results):
    print("\n" + "="*70)
    print("API性能测试汇总报告")
    print("="*70)
    
    print(f"\n{'API端点':<30} {'成功率':<10} {'平均响应':<10} {'最小':<10} {'最大':<10} {'是否达标(<3s)'}")
    print("-"*80)
    
    total_avg = []
    pass_count = 0
    total_count = 0
    
    for endpoint, result in results.items():
        if result:
            total_count += 1
            total_avg.append(result['avg_time'])
            is_pass = "✅" if result['avg_time'] < 3000 else "❌"
            print(f"{endpoint:<30} {result['success_count']}/{result['total_count']:<10} {result['avg_time']:.2f}ms {'':<10} {result['min_time']:.2f}ms {'':<10} {result['max_time']:.2f}ms {'':<10} {is_pass}")
            if result['avg_time'] < 3000:
                pass_count += 1
    
    if total_avg:
        print("-"*80)
        print(f"{'总体平均':<30} {'-':<10} {statistics.mean(total_avg):.2f}ms {'-':<20} {'✅' if statistics.mean(total_avg) < 3000 else '❌'}")
    
    print(f"\n达标率: {pass_count}/{total_count} ({pass_count/total_count*100:.1f}%)")
    
    with open('api_performance_report.json', 'w', encoding='utf-8') as f:
        json.dump({
            'test_time': datetime.now().isoformat(),
            'base_url': BASE_URL,
            'results': results,
            'summary': {
                'total_endpoints': total_count,
                'pass_count': pass_count,
                'pass_rate': pass_count/total_count*100,
                'overall_avg_response_ms': statistics.mean(total_avg) if total_avg else 0
            }
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n详细报告已保存到: api_performance_report.json")

if __name__ == "__main__":
    try:
        results = test_api_endpoints()
        generate_report(results)
    except Exception as e:
        print(f"测试失败: {e}")
        import traceback
        traceback.print_exc()