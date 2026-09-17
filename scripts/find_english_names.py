import json
import re
import os

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    
    team_attr_path = os.path.join(project_dir, 'assets', 'team_attributes.js')
    
    with open(team_attr_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    match = re.search(r'var TEAM_ATTRIBUTES = (\{[\s\S]*\});', content)
    data = json.loads(match[1])
    
    english_teams = []
    for key, team in data.items():
        name = team.get('name', '')
        if not ('\u4e00' <= name <= '\u9fff'):
            english_teams.append((key, name))
    
    print('英文名称的球队:')
    for key, name in english_teams:
        print(f'  {key}: {name}')
    
    return english_teams

if __name__ == '__main__':
    main()