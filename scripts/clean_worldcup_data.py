import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

with open(BASE_DIR / "assets" / "model-engine.js", 'r', encoding='utf-8') as f:
    content = f.read()

print(f'原始文件大小: {len(content):,} 字符')

content = re.sub(r"h2h:\s*'[^']*世界杯[^']*'", 'h2h: \'\'', content)
content = re.sub(r'h2h:\s*"[^"]*世界杯[^"]*"', 'h2h: ""', content)

content = re.sub(r"teamA:\s*'[^']*世界杯[^']*'", 'teamA: \'\'', content)
content = re.sub(r'teamA:\s*"[^"]*世界杯[^"]*"', 'teamA: ""', content)

content = re.sub(r"teamB:\s*'[^']*世界杯[^']*'", 'teamB: \'\'', content)
content = re.sub(r'teamB:\s*"[^"]*世界杯[^"]*"', 'teamB: ""', content)

content = re.sub(r"advantage:\s*'[^']*世界杯[^']*'", 'advantage: \'\'', content)
content = re.sub(r'advantage:\s*"[^"]*世界杯[^"]*"', 'advantage: ""', content)

content = re.sub(r"pressure:\s*'[^']*世界杯[^']*'", 'pressure: \'\'', content)
content = re.sub(r'pressure:\s*"[^"]*世界杯[^"]*"', 'pressure: ""', content)

content = re.sub(r"description:\s*'[^']*世界杯[^']*'", 'description: \'\'', content)
content = re.sub(r'description:\s*"[^"]*世界杯[^"]*"', 'description: ""', content)

content = re.sub(r"ambition:\s*'[^']*世界杯[^']*'", 'ambition: \'\'', content)
content = re.sub(r'ambition:\s*"[^"]*世界杯[^"]*"', 'ambition: ""', content)

content = re.sub(r"target:\s*'[^']*世界杯[^']*'", 'target: \'\'', content)
content = re.sub(r'target:\s*"[^"]*世界杯[^"]*"', 'target: ""', content)

content = re.sub(r"// ───.*世界杯.*───", '', content)

content = re.sub(r"// 基于.*世界杯.*", '', content)

content = re.sub(r"// v6.0:.*世界杯专用.*", '', content)

content = re.sub(r"// v4.5:.*世界杯.*", '', content)

content = re.sub(r"// v4.3:.*世界杯.*", '', content)

content = re.sub(r"// 方案:.*世界杯.*", '', content)

content = re.sub(r"// 问题:.*世界杯.*", '', content)

content = re.sub(r"// 基础转换矩阵.*世界杯.*", '', content)

content = re.sub(r"// 基于2026.*世界杯.*", '', content)

content = content.replace('堪称世界杯防守反击典范', '堪称防守反击典范')

content = content.replace('队史世界杯正赛首胜', '队史正赛首胜')
content = content.replace('队史第三次世界杯', '队史第三次参赛')
content = content.replace('重返世界杯', '重返大赛')
content = content.replace('首次世界杯', '首次参赛')
content = content.replace('9次世界杯经历', '9次大赛经历')
content = content.replace('世界杯报销', '受伤报销')
content = content.replace('全勤世界杯队伍', '全勤参赛队伍')
content = content.replace('世界杯淘汰赛魔咒', '淘汰赛魔咒')
content = content.replace('队史世界杯首个积分', '队史首个积分')
content = content.replace('队史世界杯破门', '队史大赛破门')
content = content.replace('世界杯小组出局魔咒', '小组出局魔咒')
content = content.replace('无缘本届世界杯', '无缘本届大赛')
content = content.replace('队史世界杯0胜', '队史大赛0胜')
content = content.replace('世界杯上首次', '大赛上首次')
content = content.replace('世界杯里程碑', '大赛里程碑')

with open(BASE_DIR / "assets" / "model-engine.js", 'w', encoding='utf-8') as f:
    f.write(content)

print(f'清理后文件大小: {len(content):,} 字符')
print('清理完成')
