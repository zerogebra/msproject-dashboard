import sqlite3, json
conn = sqlite3.connect('data/prism.db')
row = conn.execute("SELECT data FROM c2026_plan WHERE id='main'").fetchone()
plan = json.loads(row[0])
for proj in plan['projects']:
    print(f"\n=== {proj['name']} ===")
    for ph in proj['phases']:
        if ph.get('stages'):
            print(f"  STAGED: {ph['name']}")
            for st in ph['stages']:
                teams = [r['team'] for r in st['team_rows']]
                deps_sample = [(r['team'], [d['ref'] for d in r.get('depends_on',[])])
                               for r in st['team_rows'] if r.get('depends_on')]
                print(f"    {st['name']}: {teams}")
                for team, deps in deps_sample:
                    print(f"      {team} -> {deps}")
        else:
            teams = [r['team'] for r in ph.get('team_rows',[])]
            print(f"  FLAT: {ph['name']}: {teams}")
conn.close()
